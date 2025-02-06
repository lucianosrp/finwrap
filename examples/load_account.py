from finwrap import AccountCollection

accounts = AccountCollection.load("example_conf.yaml")
data = accounts.get_data().collect()
print(data)
print("=" * 50)
print(f"Total Balance: {data.select("amount").sum().item(0,0):,}")
