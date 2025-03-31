# Crypto Limit Order Book

A **Streamlit** application that displays a real-time (or simulated) limit order book for various cryptocurrencies. Users can select a cryptocurrency, choose from multiple exchanges, set the order book depth, and customize refresh intervals to see the latest bid/ask data.

## Features

- **Top 100 Cryptos**: Automatically fetches a list of the top 100 coins (by market cap) via the [CoinGecko API](https://www.coingecko.com/).
- **Binance.US Live Order Book**: For Binance (if you are in the US), it uses `binance.us` to pull live order book data.  
- **Synthetic Order Books**: For other exchanges, it simulates order book data using prices from CoinGecko.
- **Auto-Refresh**: Optional auto-refresh to keep order book data up-to-date without manual page reloads.
- **Visualization**: Interactive bar and line plots to visualize bid/ask volume and cumulative quantities.
- **Order Details**: Displays bid/ask tables, best bid/ask prices, and the spread in both absolute and percentage terms.

## Getting Started

1. **Clone the Repository**:
   ```bash
   git clone https://github.com/your-username/crypto-limit-order-book.git
   cd crypto-limit-order-book
   ```

2. **Create a Virtual Environment** (optional but recommended):
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On macOS/Linux
   # or
   venv\Scripts\activate  # On Windows
   ```

3. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Run the App**:
   ```bash
   streamlit run crypto_lob.py
   ```

5. **Configure App in Browser**:
   - Select the **cryptocurrency** from the sidebar.
   - Select the **exchange** (Binance, Coinbase, Kraken, Huobi, or KuCoin).
   - Adjust **order book depth** and **refresh rate**.
   - Optionally enable **Auto-refresh** to keep data current.

## Screenshots

*(Add or remove screenshots as you like. For example:)*

![Screenshot of main order book view](./screenshots/order_book_example.png)

## Project Structure

```
crypto-limit-order-book/
├── crypto_lob.py        # Main Streamlit application
├── requirements.txt     # Dependencies for the project
└── README.md            # This readme file
```

## Notes

- **Binance.US vs. Binance.com**: If you're located in the US, the app uses `https://api.binance.us` to avoid HTTP 451 (unavailable for legal reasons) errors.
- **Simulated Data**: For non-Binance exchanges, it simulates order book data using prices from CoinGecko.
- **Disclaimer**: This project is for demonstration and educational purposes—no trading advice is given, and data/availability can change over time.

---

*Happy coding, and enjoy the live crypto order book!*
