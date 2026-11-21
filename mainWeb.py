"""Run the SchemaMigrate Real-Time Web Dashboard."""
import argparse
import os

from schemamigrate.web.server import start_server


def main():
    parser = argparse.ArgumentParser(description="Start the SchemaMigrate Web Dashboard.")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080, help="Port to listen on (default: 8080)")
    parser.add_argument("--dsn", default=os.getenv("SCHEMAMIGRATE_DSN"), help="PostgreSQL DSN (optional)")
    args = parser.parse_args()

    start_server(host=args.host, port=args.port, dsn=args.dsn)


if __name__ == "__main__":
    main()
