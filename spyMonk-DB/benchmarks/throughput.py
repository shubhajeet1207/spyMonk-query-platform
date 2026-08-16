import time
import uuid
import ntplib
import logging
from spymonk_enterprise.client import SpyMonkClient

# Mock the NTP client to avoid hanging in local benchmarks without network
class MockNTPClient:
    def request(self, server, version=3, timeout=5):
        import time
        class MockResponse:
            def __init__(self):
                self.offset = 0.001
                self.delay = 0.005
                self.tx_time = time.time()
        return MockResponse()

ntplib.NTPClient = MockNTPClient

# Configure minimal logging for benchmark
logging.basicConfig(level=logging.WARNING)

def run_benchmark():
    print("Starting SpyMonk-DB Enterprise Benchmark (Heavy)...")
    client = SpyMonkClient("/tmp/spymonk_benchmark_heavy")
    client.start()
    
    try:
        # Pre-generate data
        num_keys = 5000
        keys = [(f"user:{i}").encode() for i in range(num_keys)]
        values = [(f"data_{uuid.uuid4()}_{'x'*100}").encode() for i in range(num_keys)]
        
        print(f"\nPhase 1: Sequential Writes ({num_keys} records)")
        start_time = time.time()
        for i in range(num_keys):
            client.put(keys[i], values[i])
        write_time = time.time() - start_time
        print(f"Time: {write_time:.2f}s")
        print(f"Throughput: {num_keys/write_time:.2f} ops/sec")
        
        print(f"\nPhase 2: Sequential Reads ({num_keys} records)")
        start_time = time.time()
        for i in range(num_keys):
            _ = client.get(keys[i])
        read_time = time.time() - start_time
        print(f"Time: {read_time:.2f}s")
        print(f"Throughput: {num_keys/read_time:.2f} ops/sec")
        
        print(f"\nPhase 3: Read-Only Transactions ({num_keys} operations)")
        start_time = time.time()
        for i in range(0, num_keys, 2):
            txn = client.begin_transaction(read_only=True)
            _ = txn.get(keys[i])
            _ = txn.get(keys[i+1])
            txn.commit()
        ro_txn_time = time.time() - start_time
        num_ro_txns = num_keys // 2
        print(f"Time: {ro_txn_time:.2f}s")
        print(f"Throughput: {num_ro_txns/ro_txn_time:.2f} txn/sec")

        print(f"\nPhase 4: Read-Write Transactions ({num_keys//10} transactions)")
        start_time = time.time()
        num_rw_txns = num_keys // 10
        for i in range(0, num_rw_txns * 2, 2):
            txn = client.begin_transaction()
            txn.put(keys[i], values[i])
            txn.put(keys[i+1], values[i+1])
            txn.commit()
        rw_txn_time = time.time() - start_time
        print(f"Time: {rw_txn_time:.2f}s")
        print(f"Throughput: {num_rw_txns/rw_txn_time:.2f} txn/sec")
        
    finally:
        client.stop()

if __name__ == "__main__":
    run_benchmark()
