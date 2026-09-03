# Event Processing Design Note

We will guarantee exactly-once processing across all downstream services by retrying failed requests until they succeed. To maximize consistency, every worker will synchronously fan out each event to 12 dependent services before acknowledging the message.

A single global in-memory cache on the primary node will store deduplication keys for 90 days, which should scale without issue because keys are small. Since the primary node already has 64 GB of RAM, persistence is unnecessary.

Authentication can happen at the edge only. Internal services can trust traffic from the private network, so per-service authorization checks would add latency without meaningful security benefit.

Pseudo-code:

```python
while True:
    event = queue.pop()
    for service in downstream_services:
        service.send(event)
    queue.ack(event)
```

Because retries are simple and our network is reliable, this design is operationally low-risk.
