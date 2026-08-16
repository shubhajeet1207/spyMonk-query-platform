"""
Directory Management

Spanner's directory is the unit of data placement and replication.
All data in a directory:
- Is replicated together (same Paxos group)
- Moves together (during rebalancing)
- Has consistent placement (geo-distribution)
"""

import hashlib
from typing import List, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


class Directory:
    """
    Spanner-style directory.

    A directory is a contiguous key range that shares:
    - Replication configuration
    - Geographic placement
    - Paxos group
    """

    def __init__(
        self,
        directory_id: str,
        key_range: Tuple[bytes, bytes],
        paxos_group_id: str,
        replication_factor: int = 3
    ):
        """
        Initialize directory.

        Args:
            directory_id: Unique directory ID
            key_range: (start_key, end_key) - end is exclusive
            paxos_group_id: ID of Paxos group replicating this directory
            replication_factor: Number of replicas
        """
        self.directory_id = directory_id
        self.key_range = key_range
        self.paxos_group_id = paxos_group_id
        self.replication_factor = replication_factor

    def contains_key(self, key: bytes) -> bool:
        """Check if key belongs to this directory"""
        start, end = self.key_range
        return start <= key < end

    def __repr__(self) -> str:
        return f"Directory(id={self.directory_id}, range={self.key_range}, group={self.paxos_group_id})"


class DirectoryManager:
    """
    Manages directories and key routing.

    Implements consistent hashing for key distribution.
    """

    def __init__(self):
        # List of directories (sorted by start key)
        self.directories: List[Directory] = []

        logger.info("Initialized directory manager")

    def create_directory(
        self,
        directory_id: str,
        key_range: Tuple[bytes, bytes],
        paxos_group_id: str,
        replication_factor: int = 3
    ) -> Directory:
        """Create a new directory"""
        directory = Directory(
            directory_id=directory_id,
            key_range=key_range,
            paxos_group_id=paxos_group_id,
            replication_factor=replication_factor
        )

        # Insert in sorted order
        self.directories.append(directory)
        self.directories.sort(key=lambda d: d.key_range[0])

        logger.info(f"Created directory {directory_id} for key range {key_range}")
        return directory

    def find_directory(self, key: bytes) -> Optional[Directory]:
        """
        Find directory containing key.

        Uses binary search on sorted directories.
        """
        left, right = 0, len(self.directories)

        while left < right:
            mid = (left + right) // 2
            directory = self.directories[mid]

            if directory.contains_key(key):
                return directory
            elif key < directory.key_range[0]:
                right = mid
            else:
                left = mid + 1

        return None

    def split_directory(self, directory_id: str, split_key: bytes) -> Tuple[Directory, Directory]:
        """
        Split a directory at split_key.

        Used for load balancing when a directory becomes too large.

        Returns:
            (left_directory, right_directory)
        """
        # Find directory
        directory = next((d for d in self.directories if d.directory_id == directory_id), None)
        if not directory:
            raise ValueError(f"Directory {directory_id} not found")

        # Remove old directory
        self.directories.remove(directory)

        # Create two new directories
        left = Directory(
            directory_id=f"{directory_id}-left",
            key_range=(directory.key_range[0], split_key),
            paxos_group_id=f"{directory.paxos_group_id}-left",
            replication_factor=directory.replication_factor
        )

        right = Directory(
            directory_id=f"{directory_id}-right",
            key_range=(split_key, directory.key_range[1]),
            paxos_group_id=f"{directory.paxos_group_id}-right",
            replication_factor=directory.replication_factor
        )

        self.directories.extend([left, right])
        self.directories.sort(key=lambda d: d.key_range[0])

        logger.info(f"Split directory {directory_id} at {split_key}")
        return (left, right)

    def get_all_directories(self) -> List[Directory]:
        """Get all directories"""
        return self.directories.copy()
