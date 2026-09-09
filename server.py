from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import random
from datetime import datetime


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
                "message": "QTXBot API is running"
            })

        elif self.path == "/api/signal":

            pairs = [
                "EURUSD-OTC",
                "GBPUSD-OTC",
                "USDJPY-OTC",
                "AUDUSD-OTC",
                "USDCAD-OTC",
                "EURJPY-OTC"
            ]

            self.send_json({
                "pair": random.choice(pairs),
                "signal": random.choice(["CALL", "PUT"]),
                "expiry": 1,
                "confidence": random.randint(70, 95),
                "time": datetime.now().strftime("%H:%M:%S")
            })

        else:
            self.send_json({
                "error": "Not Found"
            }, 404)

    def log_message(self, format, *args):
        print(
            "%s - %s" %
            (datetime.now().strftime("%H:%M:%S"),
             format % args)
        )


if __name__ == "__main__":

    server = ThreadingHTTPServer(("0.0.0.0", 8080), Handler)

    print("QTXBot API is running on port 8080")

    try:
        server.serve_forever()

    except KeyboardInterrupt:
        print("\nServer stopped")

    finally:
        server.server_close()
