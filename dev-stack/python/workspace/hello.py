"""Minimal sanity-check script for the Python dev container.

Run it from inside the container:
    docker compose -f docker-compose.dev.yml exec python bash
    python hello.py

To talk to the dev services, install clients in /workspace and connect with:
    Postgres : postgresql://dev:devpass@postgres:5432/devdb   (psycopg / SQLAlchemy)
    Redis    : redis://redis:6379/0                            (redis-py)
    Mongo    : mongodb://dev:devpass@mongo:27017               (pymongo)
    Kafka    : bootstrap_servers="kafka:19092"                 (confluent-kafka / kafka-python)
Use the service NAME as host when connecting from another container; use
localhost + the published port when connecting from your host machine.
"""
import sys


def main() -> None:
    print("Hello from the Odysseus Python dev container 👋")
    print(f"Python runtime: {sys.version.split()[0]}")
    print("Next: pip install your project deps and connect to the dev services.")


if __name__ == "__main__":
    main()
