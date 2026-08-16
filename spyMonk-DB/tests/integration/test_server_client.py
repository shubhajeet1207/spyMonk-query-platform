import unittest
import time
import threading
import shutil
import os
from pathlib import Path
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)

from spymonk_enterprise.spanserver.server import SpyMonkServer
from spymonk_enterprise.client.client import SpyMonkClient

class TestServerClientIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data_dir = "/tmp/spymonk_test_integration"
        if os.path.exists(cls.data_dir):
            shutil.rmtree(cls.data_dir)
            
        cls.server = SpyMonkServer(
            host="localhost",
            port=50052,
            data_dir=cls.data_dir,
            node_id="test-node"
        )
        cls.server_thread = threading.Thread(target=cls.server.start, daemon=True)
        cls.server_thread.start()
        time.sleep(2) # Wait for server to start

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()
        if os.path.exists(cls.data_dir):
            shutil.rmtree(cls.data_dir)

    def test_basic_kv_remote(self):
        client = SpyMonkClient("spymonk://localhost:50052")
        client.start()
        
        # Test Put
        key = b"hello"
        value = b"world"
        ts = client.put(key, value)
        self.assertIsNotNone(ts)
        
        # Test Get
        returned_value = client.get(key)
        self.assertEqual(returned_value, value)
        
        # Test Delete
        client.delete(key)
        self.assertIsNone(client.get(key))
        
        client.stop()

    def test_transaction_remote(self):
        client = SpyMonkClient("spymonk://localhost:50052")
        client.start()
        
        # Start transaction
        txn = client.begin_transaction()
        txn.put(b"txn_key", b"txn_value")
        
        # Verify isolation: value shouldn't be visible to other clients yet
        client2 = SpyMonkClient("spymonk://localhost:50052")
        client2.start()
        self.assertIsNone(client2.get(b"txn_key"))
        
        # Commit
        success = txn.commit()
        self.assertTrue(success)
        
        # Now visible
        self.assertEqual(client2.get(b"txn_key"), b"txn_value")
        
        client.stop()
        client2.stop()

    def test_scan_remote(self):
        client = SpyMonkClient("spymonk://localhost:50052")
        client.start()
        
        client.put(b"a", b"1")
        client.put(b"b", b"2")
        client.put(b"c", b"3")
        
        results = list(client.scan(start_key=b"a", end_key=b"c"))
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], (b"a", b"1"))
        self.assertEqual(results[1], (b"b", b"2"))
        
        client.stop()

    def test_sql_remote(self):
        client = SpyMonkClient("spymonk://localhost:50052")
        client.start()
        
        # Create Table
        client.execute_sql("CREATE TABLE Users (id INT64, name STRING) PRIMARY KEY (id)")
        
        # Insert Data
        client.execute_sql("INSERT INTO Users (id, name) VALUES (1, 'Alice')")
        client.execute_sql("INSERT INTO Users (id, name) VALUES (2, 'Bob')")
        
        # Select Data
        results = client.execute_sql("SELECT * FROM Users")
        self.assertEqual(len(results), 2)
        
        # Verify Bob is there
        # SQL result rows have columns mapped to bytes
        names = [row[b'name'].decode() for row in results]
        self.assertIn('Bob', names)
        
        client.stop()

if __name__ == '__main__':
    unittest.main()
