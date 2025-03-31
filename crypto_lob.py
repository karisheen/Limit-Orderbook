import streamlit as st
import pandas as pd
import numpy as np
import requests
import plotly.graph_objects as go
import time
from datetime import datetime

# Set page configuration
st.set_page_config(
    page_title="Crypto Limit Order Book",
    layout="wide"
)

# Title and description
st.title("Cryptocurrency Limit Order Book")
st.markdown("Real-time order book visualization for top cryptocurrencies")

# Sidebar for cryptocurrency selection
st.sidebar.header("Settings")

@st.cache_data(ttl=3600)  # Cache for 1 hour
def get_top_cryptos():
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {
        "vs_currency": "usd",
        "order": "market_cap_desc",
        "per_page": 100,
        "page": 1
    }
    response = requests.get(url, params=params)
    if response.status_code == 200:
        return response.json()
    else:
        st.error(f"Error fetching cryptocurrencies: {response.status_code}")
        return []

# Get top cryptos
top_cryptos = get_top_cryptos()
crypto_options = {crypto["symbol"].upper(): crypto["id"] for crypto in top_cryptos}

# Dropdown for selecting cryptocurrency
selected_symbol = st.sidebar.selectbox(
    "Select Cryptocurrency",
    options=list(crypto_options.keys()),
    index=0
)

selected_crypto_id = crypto_options[selected_symbol]

# Exchange selection
exchange_options = ["Binance", "Coinbase", "Kraken", "Huobi", "KuCoin"]
selected_exchange = st.sidebar.selectbox(
    "Select Exchange",
    options=exchange_options,
    index=0
)

# Depth selection
depth_options = [5, 10, 15, 20, 25, 50]
selected_depth = st.sidebar.selectbox(
    "Order Book Depth",
    options=depth_options,
    index=1
)

# Refresh rate
refresh_rate = st.sidebar.slider(
    "Refresh Rate (seconds)",
    min_value=1,
    max_value=60,
    value=5
)

# Function to fetch order book data
def fetch_order_book(crypto_id, exchange):
    exchange_id = exchange.lower()
    try:
        if exchange_id == "binance":
            # If you are in the US, use binance.us
            symbol = f"{selected_symbol}USDT"
            url = "https://api.binance.us/api/v3/depth"
            params = {"symbol": symbol, "limit": 500}
            response = requests.get(url, params=params)
            
            if response.status_code == 200:
                data = response.json()
                bids = pd.DataFrame(data["bids"], columns=["price", "quantity"], dtype=float)
                asks = pd.DataFrame(data["asks"], columns=["price", "quantity"], dtype=float)
                return bids, asks
            else:
                st.error(f"Error fetching order book: {response.status_code}")
                return None, None
        else:
            # Fallback to CoinGecko for other exchanges (limited / synthetic data)
            url = f"https://api.coingecko.com/api/v3/exchanges/{exchange_id}/tickers"
            params = {"coin_ids": crypto_id}
            response = requests.get(url, params=params)
            if response.status_code == 200:
                data = response.json()
                ticker = next((t for t in data["tickers"] if t["base"] == selected_symbol.upper()), None)
                if ticker:
                    mid_price = ticker["last"]
                    # Generate synthetic order book around the last price
                    bid_prices = [mid_price * (1 - 0.001 * i) for i in range(1, 51)]
                    ask_prices = [mid_price * (1 + 0.001 * i) for i in range(1, 51)]
                    bid_quantities = [np.random.uniform(0.1, 10) for _ in range(50)]
                    ask_quantities = [np.random.uniform(0.1, 10) for _ in range(50)]
                    
                    bids = pd.DataFrame({"price": bid_prices, "quantity": bid_quantities})
                    asks = pd.DataFrame({"price": ask_prices, "quantity": ask_quantities})
                    return bids, asks
                else:
                    return None, None
            else:
                st.error(f"Error fetching order book: {response.status_code}")
                return None, None
    except Exception as e:
        st.error(f"Error: {str(e)}")
        return None, None

# Create placeholder for order book visualization
order_book_chart = st.empty()
bid_ask_table = st.empty()
price_info = st.empty()

def update_order_book():
    bids, asks = fetch_order_book(selected_crypto_id, selected_exchange)
    if bids is None or asks is None:
        return
    
    # Sort and compute cumulative sums
    bids = bids.sort_values(by="price", ascending=False)
    asks = asks.sort_values(by="price", ascending=True)
    
    bids["cumulative"] = bids["quantity"].cumsum()
    asks["cumulative"] = asks["quantity"].cumsum()
    
    # Limit to selected depth
    bids = bids.head(selected_depth)
    asks = asks.head(selected_depth)
    
    fig = go.Figure()
    
    # Plot Bids
    fig.add_trace(go.Bar(
        x=bids["price"],
        y=bids["quantity"],
        name="Bids",
        marker_color="rgba(0, 128, 0, 0.7)"
    ))
    
    # Plot Asks
    fig.add_trace(go.Bar(
        x=asks["price"],
        y=asks["quantity"],
        name="Asks",
        marker_color="rgba(255, 0, 0, 0.7)"
    ))
    
    # Plot Cumulative Bids
    fig.add_trace(go.Scatter(
        x=bids["price"],
        y=bids["cumulative"],
        mode="lines",
        line=dict(width=2, color="green"),
        name="Cumulative Bids"
    ))
    
    # Plot Cumulative Asks
    fig.add_trace(go.Scatter(
        x=asks["price"],
        y=asks["cumulative"],
        mode="lines",
        line=dict(width=2, color="red"),
        name="Cumulative Asks"
    ))
    
    fig.update_layout(
        title=f"{selected_symbol}/USD Order Book on {selected_exchange}",
        xaxis_title="Price (USD)",
        yaxis_title="Quantity",
        template="plotly_dark",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        height=500
    )
    
    order_book_chart.plotly_chart(fig, use_container_width=True)
    
    # Bid/Ask Table
    col1, col2 = bid_ask_table.columns(2)
    
    with col1:
        st.subheader("Bids (Buy Orders)")
        st.dataframe(
            bids[["price", "quantity", "cumulative"]].reset_index(drop=True),
            use_container_width=True
        )
    
    with col2:
        st.subheader("Asks (Sell Orders)")
        st.dataframe(
            asks[["price", "quantity", "cumulative"]].reset_index(drop=True),
            use_container_width=True
        )
    
    # Current Price Info
    if len(asks) > 0 and len(bids) > 0:
        best_bid = bids.iloc[0]["price"]
        best_ask = asks.iloc[0]["price"]
        spread = best_ask - best_bid
        spread_percent = (spread / best_bid) * 100
        
        price_col1, price_col2, price_col3, price_col4 = price_info.columns(4)
        price_col1.metric("Best Bid", f"${best_bid:.2f}")
        price_col2.metric("Best Ask", f"${best_ask:.2f}")
        price_col3.metric("Spread", f"${spread:.2f}")
        price_col4.metric("Spread %", f"{spread_percent:.2f}%")

# Auto-refresh functionality
if st.sidebar.checkbox("Auto-refresh", value=True):
    st.sidebar.write(f"Refreshing every {refresh_rate} seconds")
    
    update_order_book()
    
    while True:
        time.sleep(refresh_rate)
        update_order_book()
        st.write(f"Last updated: {datetime.now().strftime('%H:%M:%S')}")
else:
    if st.sidebar.button("Refresh Order Book"):
        update_order_book()

st.markdown("---")
st.markdown("Data provided by Binance.US API and CoinGecko")
st.markdown("Note: For exchanges other than Binance, the order book is simulated for demonstration purposes.")
