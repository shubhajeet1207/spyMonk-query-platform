import time
import random
from locust import User, task, between, events
from spymonk_enterprise.client import SpyMonkClient

class SpyMonkDBUser(User):
    wait_time = between(0.01, 0.1) # Simulate think time

    # Class variables to share the client and initialization across workers
    client = None
    keys = []

    def on_start(self):
        """Called when a Locust user starts"""
        # We only want to initialize the client and data once per process
        if SpyMonkDBUser.client:
            return

        print("Initializing user and database connection...")
        SpyMonkDBUser.client = SpyMonkClient("/tmp/spymonk_locust")
        SpyMonkDBUser.client.start()
        
        # Pre-populate some keys for everyone to use
        SpyMonkDBUser.keys = [(f"user:{i}").encode() for i in range(1000)]
        for key in SpyMonkDBUser.keys:
            SpyMonkDBUser.client.put(key, b"init_data")

    @task(5)
    def read_operation(self):
        key = random.choice(self.keys)
        start_time = time.time()
        
        try:
            self.client.get(key)
        except Exception as e:
            events.request.fire(
                request_type="SpyMonk",
                name="get",
                response_time=(time.time() - start_time) * 1000,
                response_length=0,
                exception=e,
            )
        else:
            events.request.fire(
                request_type="SpyMonk",
                name="get",
                response_time=(time.time() - start_time) * 1000,
                response_length=len(b"init_data"),
            )

    @task(1)
    def write_operation(self):
        key = (f"user:{random.randint(1000, 2000)}").encode()
        start_time = time.time()
        
        try:
            self.client.put(key, b"new_data")
        except Exception as e:
            events.request.fire(
                request_type="SpyMonk",
                name="put",
                response_time=(time.time() - start_time) * 1000,
                response_length=0,
                exception=e,
            )
        else:
            events.request.fire(
                request_type="SpyMonk",
                name="put",
                response_time=(time.time() - start_time) * 1000,
                response_length=0,
            )

    @task(1)
    def read_only_txn(self):
        key1 = random.choice(self.keys)
        key2 = random.choice(self.keys)
        start_time = time.time()
        
        try:
            txn = self.client.begin_transaction(read_only=True)
            txn.get(key1)
            txn.get(key2)
            txn.commit()
        except Exception as e:
            events.request.fire(
                request_type="SpyMonk",
                name="ro_txn",
                response_time=(time.time() - start_time) * 1000,
                response_length=0,
                exception=e,
            )
        else:
            events.request.fire(
                request_type="SpyMonk",
                name="ro_txn",
                response_time=(time.time() - start_time) * 1000,
                response_length=0,
            )
