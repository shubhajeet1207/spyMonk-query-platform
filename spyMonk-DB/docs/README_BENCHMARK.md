# SpyMonk-DB Benchmark Guide

This database provides two primary methods for testing performance:

## 1. Quick Throughput Benchmark
A simple Python script to quickly measure sequential execution speed locally.

Run it with:
```bash
source venv/bin/activate
python3 benchmarks/throughput.py
```

## 2. Realistic Load Testing with Locust
For a more realistic test mimicking multiple clients executing concurrent read/write mix workloads, we use [Locust](https://locust.io/).

**To start a local test across 10 concurrent clients:**
```bash
source venv/bin/activate
# Runs headlessly with 10 users, adding 2 users per second, for 15 seconds.
locust -f benchmarks/locustfile.py --headless -u 10 -r 2 -t 15s
```

**For the Locust Web UI:**
```bash
source venv/bin/activate
locust -f benchmarks/locustfile.py
```
Then navigate to `http://localhost:8089` in your browser. Enter the number of users to simulate and spawn rate to see real-time charts.
