"""
Dependency Manager

Applies intra-IP and inter-IP dependencies to transaction streams.
"""

from generator import Stream


class DependencyManager:
    """
    Manages dependency creation for AXI transactions.
    """
    
    @staticmethod
    def apply_rate_limiting(stream: Stream, rate: float, bandwidth_per_cycle: float = 1.0) -> None:
        """
        Apply rate limiting by adding dependencies between consecutive requests.
        
        Only applies if rate < 1.0.
        
        Args:
            stream: Stream to apply rate limiting to
            rate: R/W rate (0.0 to 1.0)
            bandwidth_per_cycle: Bytes per cycle (default 1.0)
        """
        if rate >= 1.0:
            return  # No rate limiting needed
        
        transactions = stream.transactions
        
        for i in range(1, len(transactions)):
            # Calculate delay based on rate
            # delay = bytes / (rate * bandwidth_per_cycle)
            bytes_transferred = transactions[i-1].bytes
            delay = int(bytes_transferred / (rate * bandwidth_per_cycle))
            
            # Transaction N depends on Transaction N-1's request + delay
            transactions[i].add_dependency(
                target_id=transactions[i-1].id,
                event="req",
                offset=delay
            )
    
    @staticmethod
    def apply_outstanding_limit(stream: Stream, outstanding: int) -> None:
        """
        Apply outstanding limit by adding dependencies to earlier requests.
        
        Transaction N can only start after Transaction (N - outstanding) is requested.
        
        Args:
            stream: Stream to apply outstanding limit to
            outstanding: Maximum outstanding transactions
        """
        transactions = stream.transactions
        
        for i in range(outstanding, len(transactions)):
            # Transaction N depends on Transaction (N - outstanding)'s request
            target_idx = i - outstanding
            transactions[i].add_dependency(
                target_id=transactions[target_idx].id,
                event="req",
                offset=0
            )
    
    @staticmethod
    def apply_m2m_sync(producer: Stream, consumer: Stream, delay: int = 0) -> None:
        """
        Apply Memory-to-Memory (Frame) synchronization.
        
        Consumer's first transaction depends on producer's last response.
        
        Args:
            producer: Producer stream
            consumer: Consumer stream
            delay: Additional delay in cycles (default 0)
        """
        if len(producer) == 0 or len(consumer) == 0:
            return
        
        # Consumer's first transaction waits for producer's last response
        consumer.get_first().add_dependency(
            target_id=producer.get_last().id,
            event="resp",
            offset=delay
        )
    
    @staticmethod
    def apply_otf_sync(producer: Stream, consumer: Stream, delay: int = 0) -> None:
        """
        Apply On-The-Fly (Line) synchronization.
        
        Each line in consumer depends on corresponding line in producer.
        
        Args:
            producer: Producer stream
            consumer: Consumer stream
            delay: Additional delay in cycles (default 0)
        """
        if len(producer) == 0 or len(consumer) == 0:
            return
        
        # Get line counts
        producer_lines = producer.get_line_count()
        consumer_lines = consumer.get_line_count()
        
        # Sync each line
        num_lines = min(producer_lines, consumer_lines)
        
        for line_idx in range(num_lines):
            prod_line_txs = producer.get_line_transactions(line_idx)
            cons_line_txs = consumer.get_line_transactions(line_idx)
            
            if prod_line_txs and cons_line_txs:
                # Consumer's line start depends on producer's line start
                cons_line_txs[0].add_dependency(
                    target_id=prod_line_txs[0].id,
                    event="req",
                    offset=delay
                )
    
    @staticmethod
    def apply_m2m_group_sync(producer_streams: list, consumer_streams: list, delay: int = 0) -> None:
        """
        Apply Memory-to-Memory (Frame) synchronization for group-based dependencies.
        
        All consumer streams wait for the last-completing producer stream.
        
        Args:
            producer_streams: List of producer Stream objects in the group
            consumer_streams: List of consumer Stream objects in the group
            delay: Additional delay in cycles (default 0)
        """
        if not producer_streams or not consumer_streams:
            return
        
        # Find the last-completing transaction across all producer streams
        last_producer_tx = None
        for stream in producer_streams:
            if len(stream) > 0:
                last_tx = stream.get_last()
                if last_producer_tx is None or last_tx.id > last_producer_tx.id:
                    last_producer_tx = last_tx
        
        if last_producer_tx is None:
            return
        
        # All consumer streams' first transactions depend on this
        for stream in consumer_streams:
            if len(stream) > 0:
                stream.get_first().add_dependency(
                    target_id=last_producer_tx.id,
                    event="resp",
                    offset=delay
                )
    
    @staticmethod
    def apply_otf_group_sync(producer_streams: list, consumer_streams: list) -> None:
        """
        Apply On-The-Fly (Line) synchronization for group-based dependencies.
        
        For OTF, we apply line-by-line sync between matching streams.
        Line delay from ip_config is already applied to first transaction.
        
        Args:
            producer_streams: List of producer Stream objects in the group
            consumer_streams: List of consumer Stream objects in the group
        """
        # For OTF, typically one-to-one mapping between producer and consumer
        # Apply standard OTF sync (line_delay already applied via first tx dependency)
        for prod_stream, cons_stream in zip(producer_streams, consumer_streams):
            DependencyManager.apply_otf_sync(prod_stream, cons_stream, delay=0)
