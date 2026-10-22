import MetaTrader5 as mt5

from app.adapters.mt5 import DEFAULT_MT5_PATH


print("MT5 terminal path:", DEFAULT_MT5_PATH)

if not mt5.initialize(path=DEFAULT_MT5_PATH, timeout=60000):
    print("MT5 initialization failed:", mt5.last_error())
else:
    try:
        print("MT5 connection successful.")

        account = mt5.account_info()

        if account is None:
            print("Could not retrieve account information.")
        else:
            print("Account currency:", account.currency)
            print("Account equity:", account.equity)

        symbol = "EURUSD"

        if not mt5.symbol_select(symbol, True):
            print("Could not select EURUSD:", mt5.last_error())
        else:
            info = mt5.symbol_info(symbol)

            if info is None:
                print("Could not retrieve EURUSD specifications.")
            else:
                print("Symbol:", info.name)
                print("Tick size:", info.trade_tick_size)
                print("Tick value:", info.trade_tick_value)
                print("Minimum volume:", info.volume_min)
                print("Maximum volume:", info.volume_max)
                print("Volume step:", info.volume_step)
                print("Contract size:", info.trade_contract_size)

    finally:
        mt5.shutdown()