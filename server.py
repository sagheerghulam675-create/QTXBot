import os
import threading
import time
import json
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BINANCE_URL = "https://data-api.binance.vision/api/v3/klines?symbol={}&interval=1m&limit=100"
BINANCE_EXCHANGE_INFO_URL = "https://data-api.binance.vision/api/v3/exchangeInfo"

_pair_cache = []
_pair_cache_time = 0
_PAIR_CACHE_SECONDS = 3600


def get_binance_usdt_pairs():
    global _pair_cache, _pair_cache_time

    now = time.time()

    # Short in-memory cache
    if _pair_cache and (now - _pair_cache_time) < 3600:
        return _pair_cache

    fallback = [
        "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
        "ADAUSDT", "DOGEUSDT", "TRXUSDT", "LINKUSDT", "AVAXUSDT",
        "DOTUSDT", "LTCUSDT", "BCHUSDT", "UNIUSDT", "ATOMUSDT",
        "ETCUSDT", "XLMUSDT", "FILUSDT", "APTUSDT", "ARBUSDT",
        "OPUSDT", "NEARUSDT", "ALGOUSDT", "AAVEUSDT", "SANDUSDT",
        "MANAUSDT", "EGLDUSDT", "ICPUSDT", "HBARUSDT", "VETUSDT"
    ]

    try:
        req = urllib.request.Request(
            BINANCE_EXCHANGE_INFO_URL,
            headers={
                "User-Agent": "QTXBot/1.0",
                "Accept": "application/json"
            }
        )

        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode())

        pairs = []

        for item in data.get("symbols", []):
            symbol = str(item.get("symbol", "")).upper()
            status = str(item.get("status", "")).upper()
            quote = str(item.get("quoteAsset", "")).upper()

            if (
                symbol.endswith("USDT")
                and quote == "USDT"
                and status == "TRADING"
            ):
                pairs.append(symbol)

        pairs = sorted(set(pairs))

        if pairs:
            _pair_cache = pairs
            _pair_cache_time = now
            print(f"PAIR LIST: Binance live exchange info ({len(pairs)} pairs)")
            return pairs

        print("PAIR LIST WARNING: Binance returned no USDT trading pairs")

    except Exception as e:
        print(f"PAIR LIST ERROR: {type(e).__name__}: {e}")

    # Emergency fallback
    if _pair_cache:
        print(f"PAIR LIST CACHE: using previous cached list ({len(_pair_cache)} pairs)")
        return _pair_cache

    print(f"PAIR LIST FALLBACK: using {len(fallback)} common USDT pairs")
    _pair_cache = fallback
    _pair_cache_time = now
    return fallback

def get_binance_symbol(pair):
    pair = str(pair).upper().strip()

    # Normalize common formats:
    # BTC/USDT -> BTCUSDT
    # BTCUSDT  -> BTCUSDT
    if "/" in pair:
        base, quote = pair.split("/", 1)
        if quote == "USDT" and base:
            return base + "USDT"
        return None

    if pair.endswith("USDT") and len(pair) > 4:
        return pair

    return None


# Binance market-data endpoint.
# Use one official market-data endpoint and respect Binance rate-limit bans.
BINANCE_KLINE_BASE = "https://data-api.binance.vision"

_candle_cache = {}
_CANDLE_CACHE_SECONDS = 15

# If Binance returns 418/429, do not keep rotating endpoints.
_binance_backoff_until = 0


def get_candles(symbol):
    global _binance_backoff_until

    now = time.time()

    # Avoid repeatedly requesting the same 100 candles.
    cached = _candle_cache.get(symbol)
    if cached:
        cached_time, cached_data = cached
        if now - cached_time < _CANDLE_CACHE_SECONDS:
            return cached_data

    def get_coinbase_symbol(binance_symbol):
        symbol = str(binance_symbol).upper().strip()

        if symbol.endswith("USDT"):
            base = symbol[:-4]
        else:
            base = symbol

        # Coinbase USD products available for common Binance assets.
        return f"{base}-USD"

    def get_coinbase_candles(binance_symbol):
        product = get_coinbase_symbol(binance_symbol)

        url = (
            "https://api.exchange.coinbase.com/products/"
            f"{product}/candles?granularity=60"
        )

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "QTXBot/1.0",
                "Accept": "application/json"
            }
        )

        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode())

        if not isinstance(data, list) or len(data) < 30:
            raise ValueError(
                f"Coinbase returned insufficient candles for {product}"
            )

        # Coinbase format:
        # [time, low, high, open, close, volume]
        #
        # Sort oldest -> newest because the signal engine expects
        # chronological candle data.
        data = sorted(data, key=lambda x: x[0])

        candles = [
            {
                "open": float(x[3]),
                "high": float(x[2]),
                "low": float(x[1]),
                "close": float(x[4]),
                "volume": float(x[5])
            }
            for x in data
        ]

        return candles

    # ------------------------------------------------------------
    # PRIMARY: Binance
    # ------------------------------------------------------------
    try:
        if now >= _binance_backoff_until:
            url = (
                f"{BINANCE_KLINE_BASE}/api/v3/klines"
                f"?symbol={symbol}&interval=1m&limit=100"
            )

            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "QTXBot/1.0",
                    "Accept": "application/json"
                }
            )

            with urllib.request.urlopen(req, timeout=8) as response:
                data = json.loads(response.read().decode())

            if not isinstance(data, list) or len(data) < 30:
                raise ValueError("Not enough Binance market candles")

            candles = [
                {
                    "open": float(x[1]),
                    "high": float(x[2]),
                    "low": float(x[3]),
                    "close": float(x[4]),
                    "volume": float(x[5])
                }
                for x in data
            ]

            _candle_cache[symbol] = (time.time(), candles)

            print(
                f"MARKET DATA: Binance live candles for {symbol}",
                flush=True
            )

            return candles

    except urllib.error.HTTPError as e:
        print(
            f"BINANCE CANDLE ERROR: HTTP {e.code} -> {e}",
            flush=True
        )

        if e.code in (418, 429):
            _binance_backoff_until = time.time() + 30

    except Exception as e:
        print(
            f"BINANCE CANDLE ERROR: {type(e).__name__}: {e}",
            flush=True
        )

    # ------------------------------------------------------------
    # FALLBACK: Coinbase
    # ------------------------------------------------------------
    try:
        candles = get_coinbase_candles(symbol)

        _candle_cache[symbol] = (time.time(), candles)

        print(
            f"MARKET DATA FALLBACK: Coinbase live candles for {symbol}",
            flush=True
        )

        return candles

    except Exception as e:
        print(
            f"COINBASE FALLBACK ERROR: {type(e).__name__}: {e}",
            flush=True
        )

        raise RuntimeError(
            f"Market data unavailable: Binance and Coinbase failed"
        )

def ema(values, period):
    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)
    result = sum(values[:period]) / period

    for price in values[period:]:
        result = ((price - result) * multiplier) + result

    return result

def calculate_rsi(values, period=14):
    if len(values) <= period:
        return 50.0

    gains = []
    losses = []

    for i in range(1, len(values)):
        change = values[i] - values[i - 1]

        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def analyze_market(candles):
    closes = [c["close"] for c in candles]

    if len(closes) < 30:
        raise ValueError("Not enough market candles")

    price = closes[-1]
    ema9 = ema(closes, 9)
    ema21 = ema(closes, 21)
    rsi = calculate_rsi(closes, 14)

    if ema9 is None or ema21 is None:
        raise ValueError("EMA calculation unavailable")

    # Short-term momentum
    momentum = closes[-1] - closes[-6]

    last_open = candles[-1]["open"]
    last_close = candles[-1]["close"]
    last_high = candles[-1]["high"]
    last_low = candles[-1]["low"]

    score = 0

    # -------------------------------------------------
    # 1. MAIN TREND
    # -------------------------------------------------
    if ema9 > ema21:
        trend = "BULLISH"
        score += 2
    elif ema9 < ema21:
        trend = "BEARISH"
        score -= 2
    else:
        trend = "NEUTRAL"

    # -------------------------------------------------
    # 2. PRICE LOCATION
    # -------------------------------------------------
    if price > ema21:
        score += 1
    elif price < ema21:
        score -= 1

    # -------------------------------------------------
    # 3. EMA GAP
    # Require meaningful separation.
    # -------------------------------------------------
    ema_gap = abs(ema9 - ema21) / ema21 * 100

    if ema_gap >= 0.03:
        if ema9 > ema21:
            score += 1
        elif ema9 < ema21:
            score -= 1

    # -------------------------------------------------
    # 4. RSI
    # Avoid chasing extreme conditions.
    # -------------------------------------------------
    if 52 <= rsi <= 68:
        score += 1
    elif 32 <= rsi <= 48:
        score -= 1
    elif rsi > 75:
        score -= 1
    elif rsi < 25:
        score += 1

    # -------------------------------------------------
    # 5. MOMENTUM
    # -------------------------------------------------
    if momentum > 0:
        score += 1
    elif momentum < 0:
        score -= 1

    # -------------------------------------------------
    # 6. LAST CANDLE
    # -------------------------------------------------
    candle_range = last_high - last_low
    candle_body = abs(last_close - last_open)

    if last_close > last_open:
        candle = "BULLISH"

        if candle_range > 0 and candle_body / candle_range >= 0.55:
            score += 1

    elif last_close < last_open:
        candle = "BEARISH"

        if candle_range > 0 and candle_body / candle_range >= 0.55:
            score -= 1

    else:
        candle = "NEUTRAL"

    # -------------------------------------------------
    # 7. CONFIRMATION FILTERS
    # -------------------------------------------------
    # Balanced RSI confirmation:
    # Allow moderately overbought/oversold conditions when
    # the trend, momentum, EMA gap and candle all agree.
    bullish_confirmed = (
        ema9 > ema21
        and price > ema21
        and momentum > 0
        and 50 < rsi < 80
        and ema_gap >= 0.03
    )

    bearish_confirmed = (
        ema9 < ema21
        and price < ema21
        and momentum < 0
        and 20 < rsi < 50
        and ema_gap >= 0.03
    )

    # Candle direction must also agree when a real body exists.
    bullish_candle_ok = (
        candle == "BULLISH"
        and candle_range > 0
        and candle_body / candle_range >= 0.55
    )

    bearish_candle_ok = (
        candle == "BEARISH"
        and candle_range > 0
        and candle_body / candle_range >= 0.55
    )

    # -------------------------------------------------
    # 7. BALANCED SIGNAL DECISION
    # -------------------------------------------------
    # Trend + price + momentum remain important.
    # Candle is supporting evidence, not a hard blocker.
    # This avoids excessive NO SIGNAL results while
    # still rejecting weak/conflicting setups.

    bullish_setup = (
        ema9 > ema21
        and price > ema21
        and momentum > 0
        and 50 < rsi < 75
    )

    bearish_setup = (
        ema9 < ema21
        and price < ema21
        and momentum < 0
        and 25 < rsi < 50
    )

    bullish_points = 0
    bearish_points = 0

    if ema9 > ema21:
        bullish_points += 1
    elif ema9 < ema21:
        bearish_points += 1

    if price > ema21:
        bullish_points += 1
    elif price < ema21:
        bearish_points += 1

    if momentum > 0:
        bullish_points += 1
    elif momentum < 0:
        bearish_points += 1

    if ema_gap >= 0.03:
        if ema9 > ema21:
            bullish_points += 1
        elif ema9 < ema21:
            bearish_points += 1

    if 50 < rsi < 75:
        bullish_points += 1
    elif 25 < rsi < 50:
        bearish_points += 1

    if bullish_candle_ok:
        bullish_points += 1
    elif bearish_candle_ok:
        bearish_points += 1

    if bullish_setup and bullish_points >= 4 and bullish_points > bearish_points:
        signal = "CALL"

    elif bearish_setup and bearish_points >= 4 and bearish_points > bullish_points:
        signal = "PUT"

    else:
        signal = "NO SIGNAL"

    # -------------------------------------------------
    # 8. CONFIDENCE
    # Confidence represents analysis strength,
    # not a guarantee of outcome.
    # -------------------------------------------------
    if signal == "CALL":
        confidence = min(90, 65 + (bullish_points - 4) * 8)

    elif signal == "PUT":
        confidence = min(90, 65 + (bearish_points - 4) * 8)

    else:
        confidence = 0

    return {
        "signal": signal,
        "confidence": confidence,
        "price": round(price, 8),
        "ema9": round(ema9, 8),
        "ema21": round(ema21, 8),
        "rsi": round(rsi, 2),
        "momentum": round(momentum, 8),
        "score": score,
        "trend": trend,
        "candle": candle,
        "ema_gap": round(ema_gap, 4)
    }


latest_signals = {}

def background_monitor():
    pairs = ["BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT"]
    pair_map = {
        "BTC/USDT": "BTCUSDT",
        "ETH/USDT": "ETHUSDT",
        "BNB/USDT": "BNBUSDT",
        "SOL/USDT": "SOLUSDT",
        "XRP/USDT": "XRPUSDT"
    }

    while True:
        for pair in pairs:
            try:
                candles = get_candles(pair_map[pair])
                result = analyze_market(candles)
                latest_signals[pair] = {
                    **result,
                    "pair": pair,
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "data_verified": True,
                    "source": "Binance live market candles"
                }
            except Exception as e:
                latest_signals[pair] = {
                    "pair": pair,
                    "signal": "NO SIGNAL",
                    "confidence": 0,
                    "data_verified": False,
                    "status": "DATA ERROR",
                    "error": str(e),
                    "time": datetime.now().strftime("%H:%M:%S")
                }
        time.sleep(60)

class Handler(BaseHTTPRequestHandler):

    protocol_version = "HTTP/1.1"

    def send_json(self, data, status=200):
        body = json.dumps(data).encode("utf-8")

        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            self.close_connection = True

    def do_GET(self):

        if self.path == "/":
            self.send_json({
                "status": "ok",
                "message": "QTXBot Live Analysis API",
                "source": "Binance live market candles"
            })
            return

        if self.path.startswith("/api/activate"):
            try:
                from urllib.parse import urlparse, parse_qs

                qs = parse_qs(urlparse(self.path).query)
                key = qs.get("key", [""])[0].strip().upper()

                valid_keys = {
                    "QTX-8F4K-29PM": True,
                }

                if valid_keys.get(key) is True:
                    self.send_json({
                        "status": "ok",
                        "activated": True,
                        "message": "QTXBot activated successfully"
                    })
                else:
                    self.send_json({
                        "status": "error",
                        "activated": False,
                        "message": "Invalid or disabled activation key"
                    })

            except Exception as e:
                self.send_json({
                    "status": "error",
                    "activated": False,
                    "message": str(e)
                })

            return

        if self.path == "/api/pairs":
            try:
                pairs = get_binance_usdt_pairs()

                self.send_json({
                    "status": "ok",
                    "source": "Binance live exchange info / cached fallback",
                    "count": len(pairs),
                    "pairs": pairs
                })
            except Exception as e:
                self.send_json({
                    "status": "error",
                    "count": 0,
                    "pairs": [],
                    "error": "Unable to load market pairs",
                    "details": str(e)
                }, 503)
            return

        if self.path.startswith("/api/signal"):

            from urllib.parse import urlparse, parse_qs

            query = parse_qs(urlparse(self.path).query)

            pair = query.get("pair", ["BTC/USDT"])[0].upper()
            expiry = query.get("expiry", ["1 MIN"])[0].upper()

            allowed_expiry = {
                "3 SEC": 3,
                "5 SEC": 5,
                "10 SEC": 10,
                "15 SEC": 15,
                "30 SEC": 30,
                "1 MIN": 60,
                "2 MIN": 120,
                "3 MIN": 180,
                "5 MIN": 300,
                "10 MIN": 600,
                "15 MIN": 900,
                "1 HOUR": 3600
            }

            if expiry not in allowed_expiry:
                self.send_json({
                    "signal": "NO SIGNAL",
                    "confidence": 0,
                    "error": "Unsupported expiry"
                }, 400)
                return

            symbol = get_binance_symbol(pair)

            if symbol is None:
                self.send_json({
                    "signal": "NO SIGNAL",
                    "confidence": 0,
                    "error": "Unsupported pair",
                    "pair": pair
                }, 400)
                return

            try:
                candles = get_candles(symbol)
                result = analyze_market(candles)

                self.send_json({
                    "pair": pair,
                    "signal": result["signal"],
                    "confidence": result["confidence"],
                    "expiry": expiry,
                    "expiry_seconds": allowed_expiry[expiry],
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "price": result["price"],
                    "ema9": result["ema9"],
                    "ema21": result["ema21"],
                    "rsi": result["rsi"],
                    "momentum": result["momentum"],
                    "score": result["score"],
                    "trend": result["trend"],
                    "candle": result["candle"],
                    "data_verified": True,
                    "source": "Binance live market candles",
                    "analysis": "EMA9 + EMA21 + RSI + Momentum + EMA Gap + Candle Strength",
                    "status": "LIVE"
                })

            except Exception as e:
                self.send_json({
                    "pair": pair,
                    "signal": "NO SIGNAL",
                    "confidence": 0,
                    "data_verified": False,
                    "status": "DATA ERROR",
                    "error": "Market data unavailable",
                    "details": str(e),
                    "time": datetime.now().strftime("%H:%M:%S")
                }, 503)

            return

        self.send_json({"error": "Not Found"}, 404)

    def log_message(self, format, *args):
        print(
            "%s - %s" %
            (datetime.now().strftime("%H:%M:%S"), format % args)
        )

port = int(os.environ.get("PORT", 8080))

server = ThreadingHTTPServer(
    ("0.0.0.0", port),
    Handler
)

monitor_thread = threading.Thread(
    target=background_monitor,
    daemon=True
)
monitor_thread.start()

print("QTXBot Live Analysis API is running on port", port)

server.serve_forever()
