"""Paper trading. After each close the agents decide and orders are queued; at the next
open the queue is checked against live quotes and the risk engine and sent to the
broker. Nothing here can trade real money: the only broker classes are the Alpaca PAPER
endpoint and a local simulator, and every fill is stored with which of the two made it."""
