import requests
import ccxt
import time
import logging
from pymongo import MongoClient
from flask import Flask, render_template, request, redirect, url_for, flash
from threading import Thread
from datetime import datetime  # Import datetime

# Configure logging
logging.basicConfig(filename='arbitrage_monitor.log', level=logging.INFO, format='%(asctime)s:%(levelname)s:%(message)s')

# MongoDB Atlas configuration
mongo_client = MongoClient("mongodb+srv://Omer:12345@cluster0.7yosb.mongodb.net/?retryWrites=true&w=majority&appName=Arbitrage")
db = mongo_client['arbitrage_db']
coins_collection = db['monitored_coins']
opportunities_collection = db['arbitrage_opportunities']

# Telegram configuration
TELEGRAM_TOKEN = "8082113315:AAHckCvgfsBLqq3tgzyuS2nh_MINaL5xkmQ"
CHAT_ID = "5981216938"

# Function to send Telegram notifications
def send_telegram_notification(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        'chat_id': CHAT_ID,
        'text': message
    }
    try:
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            logging.info(f"Telegram notification sent: {message}")
        return response.status_code == 200
    except Exception as e:
        logging.error(f"Failed to send notification: {e}")
        return False

# Initialize Flask app
app = Flask(__name__)
app.secret_key = 'your_secret_key'  # Set a secret key for session management

def fetch_price_from_dexscreener(chain_id, pair_id):
    """Fetch price from Dexscreener."""
    url = f"https://api.dexscreener.com/latest/dex/pairs/{chain_id}/{pair_id}"
    response = requests.get(url)
    
    if response.status_code != 200:
        logging.error(f"Error fetching data from Dexscreener: {response.status_code}")
        return None

    data = response.json()

    if "pair" in data and data["pair"] is not None:
        price_usd = data["pair"]["priceUsd"]
        try:
            return float(price_usd)
        except ValueError:
            logging.error(f"Error converting Dexscreener price to float: {price_usd}")
            return None
    else:
        logging.warning(f"No pair data found for chain ID: {chain_id}, pair ID: {pair_id}")
        return None

def fetch_price_from_mexc(symbol):
    """Fetch price from MEXC."""
    exchange = ccxt.mexc()
    try:
        ticker = exchange.fetch_ticker(symbol)
        last_price = ticker.get('last')

        if isinstance(last_price, (int, float)):
            return float(last_price)
        elif isinstance(last_price, str) and last_price.replace('.', '', 1).isdigit():
            return float(last_price)
        else:
            logging.error(f"Unexpected type for last price: {type(last_price)}. Value: {last_price}")
            return None

    except Exception as e:
        logging.error(f"Error fetching price from MEXC for {symbol}: {e}")
        return None

@app.route('/')
def index():
    coins = list(coins_collection.find({}, {'_id': 0}))  # Get coins without the MongoDB ID
    opportunities = list(opportunities_collection.find({}, {'_id': 0}))  # Get opportunities without the MongoDB ID
    return render_template('index.html', coins=coins, opportunities=opportunities)

@app.route('/add_coin', methods=['POST'])
def add_coin():
    """Allow users to add a coin to monitor."""
    chain_id = request.form.get("chain_id")
    pair_id = request.form.get("pair_id")
    symbol = request.form.get("symbol")

    # Validate inputs
    if not chain_id or not pair_id or not symbol:
        flash("Invalid input. All fields are required.")
        return redirect(url_for('index'))

    # Store coin in MongoDB
    coins_collection.insert_one({
        "chain_id": chain_id,
        "pair_id": pair_id,
        "symbol": symbol
    })

    logging.info(f"Added coin: {symbol} on {chain_id} with pair ID {pair_id}")
    flash(f"Added coin: {symbol} on {chain_id} with pair ID {pair_id}")
    return redirect(url_for('index'))

def monitor_coins(coins, start_index, end_index, investment_amount):
    """Monitor coins for arbitrage opportunities."""
    for i in range(start_index, end_index):
        coin = coins[i]
        chain_id = coin["chain_id"]
        pair_id = coin["pair_id"]
        symbol = coin["symbol"]

        # Fetch prices
        dex_usd_price = fetch_price_from_dexscreener(chain_id, pair_id)
        mexc_price = fetch_price_from_mexc(symbol)

        # Check if prices were fetched successfully
        if dex_usd_price is not None and mexc_price is not None:
            logging.info(f"\nMonitoring: {symbol}")
            logging.info(f"Dexscreener Price (USD): {dex_usd_price}")
            logging.info(f"MEXC Price: {mexc_price}")

            # Calculate arbitrage opportunity
            if mexc_price < dex_usd_price:  # Buy from MEXC, Sell on Dexscreener
                units_bought = investment_amount / mexc_price
                potential_revenue = units_bought * dex_usd_price
                profit = potential_revenue - investment_amount

                if profit >= 5:  # Minimum profit condition
                    percentage_difference = ((dex_usd_price - mexc_price) / mexc_price) * 100
                    if percentage_difference >= 10:  # Minimum percentage difference
                        opportunity = {
                            "type": "MEXC to Dexscreener",
                            "buy_price": mexc_price,
                            "sell_price": dex_usd_price,
                            "profit": profit,
                            "percentage_difference": percentage_difference,
                            "timestamp": datetime.fromtimestamp(time.time()).strftime('%Y-%m-%d %H:%M:%S'),
                            "coin": symbol
                        }
                        opportunities_collection.insert_one(opportunity)  # Store opportunity in MongoDB
                        
                        logging.info(f"Buy from MEXC at {mexc_price} and sell on Dexscreener at {dex_usd_price}.")
                        logging.info(f"Potential profit: ${profit:.12f}")
                        logging.info(f"Percentage difference: {percentage_difference:.12f}%")
                        
                        # Send notification to Telegram
                        notification_message = (
                            f"Arbitrage opportunity found:\n"
                            f"Symbol: {symbol}\n"
                            f"MEXC Price: {mexc_price:.12f}\n"
                            f"Dexscreener Price: {dex_usd_price:.12f}\n"
                            f"Profit: ${profit:.12f}\n"
                            f"Timestamp: {opportunity['timestamp']}"
                        )
                        send_telegram_notification(notification_message)

            elif dex_usd_price < mexc_price:  # Buy from Dexscreener, Sell on MEXC
                units_bought = investment_amount / dex_usd_price
                potential_revenue = units_bought * mexc_price
                profit = potential_revenue - investment_amount

                if profit >= 5:  # Minimum profit condition
                    percentage_difference = ((mexc_price - dex_usd_price) / dex_usd_price) * 100
                    if percentage_difference >= 10:  # Minimum percentage difference
                        opportunity = {
                            "type": "Dexscreener to MEXC",
                            "buy_price": dex_usd_price,
                            "sell_price": mexc_price,
                            "profit": profit,
                            "percentage_difference": percentage_difference,
                            "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                            "coin": symbol
                        }
                        opportunities_collection.insert_one(opportunity)  # Store opportunity in MongoDB
                        
                        logging.info(f"Buy from Dexscreener at {dex_usd_price} and sell on MEXC at {mexc_price}.")
                        logging.info(f"Potential profit: ${profit:.12f}")
                        logging.info(f"Percentage difference: {percentage_difference:.12f}%")
                        
                        # Send notification to Telegram
                        notification_message = (
                            f"Arbitrage opportunity found:\n"
                            f"Symbol: {symbol}\n"
                            f"Dexscreener Price: {dex_usd_price:.12f}\n"
                            f"MEXC Price: {mexc_price:.12f}\n"
                            f"Profit: ${profit:.12f}\n"
                            f"Timestamp: {opportunity['timestamp']}"
                        )
                        send_telegram_notification(notification_message)

            else:
                logging.info("Prices are too close. No arbitrage opportunity.")

        else:
            logging.warning(f"Failed to fetch prices for {symbol}")

def main():
    investment_amount = 50  # Amount you want to invest in USD

    while True:
        # Retrieve coins from MongoDB each time the loop runs
        coins = list(coins_collection.find({}, {'_id': 0}))  # Get coins without the MongoDB ID

        if not coins:
            logging.warning("No coins to monitor. Sleeping for 30 seconds...")
            time.sleep(30)
            continue  # Skip the rest of the loop if there are no coins

        total_coins = len(coins)
        for start_index in range(0, total_coins, 100):
            end_index = min(start_index + 100, total_coins)
            logging.info(f"\nMonitoring coins {start_index + 1} to {end_index}...")
            monitor_coins(coins, start_index, end_index, investment_amount)
        
        logging.info("Finished monitoring all coins. Sleeping for 30 seconds.")
        time.sleep(30)  # Monitor every 30 seconds

if __name__ == '__main__':
    # Start monitoring thread
    monitoring_thread = Thread(target=main)
    monitoring_thread.start()

    # Run Flask app
    app.run(debug=True, host='0.0.0.0', port=5000)
