import assert from 'node:assert/strict'
import test from 'node:test'
import { BinanceFuturesAdapter } from './orderflow/lib/adapters/binanceFutures'
import { BybitFuturesAdapter, BybitSpotAdapter, HyperliquidAdapter, BitgetFuturesAdapter, BitgetSpotAdapter, CoinbaseSpotAdapter } from './orderflow/lib/adapters/exchanges'
import { FinnhubForexAdapter } from './orderflow/lib/adapters/forex'
import type { NormalizedOrderBook, NormalizedTrade } from './orderflow/lib/types/market'

class BybitPerp extends BybitFuturesAdapter { ingest(payload: unknown) { this.handleMessage(payload) } }
class BybitSpot extends BybitSpotAdapter { ingest(payload: unknown) { this.handleMessage(payload) } }
class BinancePerp extends BinanceFuturesAdapter { ingest(payload: unknown) { this.handleMessage(payload) } }
class Hyperliquid extends HyperliquidAdapter { ingest(payload: unknown) { this.handleMessage(payload) } }
class BitgetPerp extends BitgetFuturesAdapter { ingest(payload: unknown) { this.handleMessage(payload) } }
class BitgetSpot extends BitgetSpotAdapter { ingest(payload: unknown) { this.handleMessage(payload) } }
class Coinbase extends CoinbaseSpotAdapter { ingest(payload: unknown) { this.handleMessage(payload) } }
class Forex extends FinnhubForexAdapter { ingest(payload: unknown) { this.handleMessage(payload) } }

for (const Adapter of [BybitPerp, BybitSpot]) {
  test(`${Adapter.name}: noncontiguous update IDs retain depth; restart snapshots replace it`, () => {
    const adapter = new Adapter('BTCUSDT')
    const books: NormalizedOrderBook[] = []
    adapter.on('orderbook', book => books.push(book))
    const payload = (type: string, u: number, b: string[][], a: string[][]) => ({ topic: 'orderbook.50.BTCUSDT', type, data: { s: 'BTCUSDT', u, seq: u, b, a } })
    adapter.ingest(payload('snapshot', 100, [['100', '2']], [['101', '3']]))
    adapter.ingest(payload('delta', 110, [['100', '4']], []))
    assert.equal(books.length, 2)
    assert.equal(books[1]?.bids[0]?.size, 4)
    assert.equal(books[1]?.asks[0]?.size, 3)
    adapter.ingest(payload('delta', 109, [['100', '99']], []))
    assert.equal(books.length, 2)
    adapter.ingest(payload('snapshot', 1, [['98', '1']], [['99', '2']]))
    assert.deepEqual(books[2]?.bids.map(level => level.price), [98])
    assert.deepEqual(books[2]?.asks.map(level => level.price), [99])
  })
}

test('Binance futures consumes b/a partial-depth snapshots and replaces prior levels', () => {
  const adapter = new BinancePerp('BTCUSDT')
  const books: NormalizedOrderBook[] = []
  adapter.on('orderbook', book => books.push(book))
  adapter.ingest({ e: 'depthUpdate', U: 10, u: 20, pu: 9, b: [['100', '2']], a: [['101', '3']] })
  adapter.ingest({ e: 'depthUpdate', U: 21, u: 30, pu: 20, b: [['99', '4']], a: [['102', '5']] })
  assert.equal(books.length, 2)
  assert.deepEqual(books[1]?.bids.map(({ price, size }) => [price, size]), [[99, 4]])
  assert.deepEqual(books[1]?.asks.map(({ price, size }) => [price, size]), [[102, 5]])
})

test('Hyperliquid consumes px/sz object levels', () => {
  const adapter = new Hyperliquid('BTCUSDT')
  const books: NormalizedOrderBook[] = []
  adapter.on('orderbook', book => books.push(book))
  adapter.ingest({ channel: 'l2Book', data: { coin: 'BTC', time: 1700000000000, levels: [[{ px: '100', sz: '2', n: 1 }], [{ px: '101', sz: '3', n: 2 }]] } })
  assert.equal(books.length, 1)
  assert.equal(books[0]?.bids[0]?.size, 2)
  assert.equal(books[0]?.asks[0]?.price, 101)
})

for (const Adapter of [BitgetPerp, BitgetSpot]) {
  test(`${Adapter.name}: object trades retain taker side and unique trade ID`, () => {
    const adapter = new Adapter('BTCUSDT')
    const trades: NormalizedTrade[] = []
    adapter.on('trade', trade => trades.push(trade))
    adapter.ingest({ arg: { channel: 'trade' }, data: [
      { ts: '1700000000000', price: '100', size: '2', side: 'buy', tradeId: 'first' },
      { ts: '1700000000000', price: '101', size: '3', side: 'sell', tradeId: 'second' },
    ] })
    assert.deepEqual(trades.map(({ price, quantity, side }) => ({ price, quantity, side })), [
      { price: 100, quantity: 2, side: 'buy' }, { price: 101, quantity: 3, side: 'sell' },
    ])
    assert.notEqual(trades[0]?.id, trades[1]?.id)
  })
}

test('Coinbase maker side is inverted into aggressor side', () => {
  const adapter = new Coinbase('BTCUSDT')
  const trades: NormalizedTrade[] = []
  adapter.on('trade', trade => trades.push(trade))
  adapter.ingest({ channel: 'market_trades', events: [{ trades: [
    { trade_id: '1', product_id: 'BTC-USD', price: '100', size: '2', side: 'BUY', time: '2026-11-06T15:00:00Z' },
    { trade_id: '2', product_id: 'BTC-USD', price: '101', size: '3', side: 'SELL', time: '2026-11-06T15:00:01Z' },
  ] }] })
  assert.deepEqual(trades.map(trade => trade.side), ['sell', 'buy'])
})

test('Finnhub FX labels synthetic quote/depth and tick-rule flow as proxies', () => {
  const adapter = new Forex('EUR/USD')
  const trades: NormalizedTrade[] = []
  const books: NormalizedOrderBook[] = []
  adapter.on('trade', trade => trades.push(trade))
  adapter.on('orderbook', book => books.push(book))
  const timestamp = Date.parse('2026-11-06T15:00:00Z')
  adapter.ingest({ type: 'trade', data: [{ p: 1.1, t: timestamp }, { p: 1.09, t: timestamp + 1000, v: 2 }] })
  assert.equal(trades.length, 2)
  assert.equal(books.length, 2)
  assert.deepEqual(trades.map(trade => trade.side), ['buy', 'sell'])
  assert.deepEqual(trades.map(trade => trade.quantity), [1, 2])
  assert.ok(trades.every(trade => trade.dataBasis === 'fx-proxy'))
  assert.ok(books.every(book => book.dataBasis === 'fx-proxy'))
  assert.ok((books[0]?.bids[0]?.price ?? Infinity) < 1.1)
  assert.ok((books[0]?.asks[0]?.price ?? -Infinity) > 1.1)
  adapter.ingest({ type: 'trade', data: [{ p: 1.1, t: Date.parse('2026-11-07T15:00:00Z') }] })
  assert.equal(trades.length, 2)
  assert.match(adapter.getStatus().error ?? '', /MARKET CLOSED/)
})

test('Binance routed sockets require both feeds and ignore replaced socket events', t => {
  const sockets: Socket[] = []
  class Socket {
    static OPEN = 1
    readyState = 0
    onopen: (() => void) | null = null
    onclose: (() => void) | null = null
    onerror: (() => void) | null = null
    onmessage: ((event: { data: string }) => void) | null = null
    sent: string[] = []
    constructor(readonly url: string) { sockets.push(this) }
    send(data: string) { this.sent.push(data) }
    close() { this.readyState = 3 }
    open() { this.readyState = Socket.OPEN; this.onopen?.() }
  }
  // Controlled sockets expose only the WebSocket surface consumed by the adapter.
  const originalSocket = globalThis.WebSocket
  globalThis.WebSocket = Socket as unknown as typeof WebSocket
  t.after(() => { globalThis.WebSocket = originalSocket })
  const adapter = new BinanceFuturesAdapter('BTCUSDT')
  const trades: NormalizedTrade[] = []
  adapter.on('trade', trade => trades.push(trade))
  try {
    adapter.connect()
    const book = sockets[0]!
    const market = sockets[1]!
    assert.equal(book.url, 'wss://fstream.binance.com/public/ws')
    assert.equal(market.url, 'wss://fstream.binance.com/market/ws')
    book.open()
    assert.equal(adapter.getStatus().status, 'connecting')
    market.open()
    assert.equal(adapter.getStatus().status, 'live')
    const print = JSON.stringify({ e: 'aggTrade', p: '100', q: '2', a: 1, s: 'BTCUSDT', T: 1000, m: true })
    market.onmessage?.({ data: print })
    assert.equal(trades[0]?.side, 'sell')
    adapter.connect()
    assert.equal(book.readyState, 3)
    assert.equal(market.readyState, 3)
    sockets[2]!.open()
    sockets[3]!.open()
    market.onclose?.()
    market.onmessage?.({ data: print })
    assert.equal(adapter.getStatus().status, 'live')
    assert.equal(trades.length, 1)
  } finally {
    adapter.disconnect()
  }
  assert.equal(adapter.getStatus().status, 'offline')
  assert.ok(sockets.every(socket => socket.readyState === 3))
})

test('Forex reports the missing credential before attempting a socket', () => {
  const adapter = new FinnhubForexAdapter('EUR/USD')
  adapter.connect()
  assert.equal(adapter.getStatus().status, 'error')
  assert.match(adapter.getStatus().error ?? '', /Missing VITE_FINNHUB_API_KEY/)
  adapter.disconnect()
})
