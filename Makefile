.PHONY: install test lint clean

install:
    pip install -r requirements.txt

test:
    python -m pytest tests/ -q

lint:
    flake8 .

clean:
    find . -type d -name __pycache__ -exec rm -rf {} +
    find . -type f -name "*.pyc" -delete