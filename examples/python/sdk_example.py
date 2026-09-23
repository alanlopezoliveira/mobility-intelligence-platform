from src.sdk.python.client import MobilityClient

client = MobilityClient('http://localhost:8000')
print(client.model_info())
print(client.stations())
