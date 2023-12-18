import datetime

import queue

import numpy as np
import pandas as pd
from Event import FillEvent, OrderEvent, SignalEvent
from Performance import create_sharpe_ratio, create_drawdowns
from math import floor

class Portfolio():
    """
    The Portfolio class handles the positions and market
    value of all instruments at a resolution of a "bar",
    i.e. secondly, minutely, 5-min, 30-min, 60 min or EOD.
    The positions DataFrame stores a time-index of the
    quantity of positions held.
    The holdings DataFrame stores the cash and total market
    holdings value of each symbol for a particular
    time-index, as well as the percentage change in
    portfolio total across bars.
    """

    def __init__(self, bars, events, window, initial_capital=100000.0):
        """
        Initialises the portfolio with bars and an event queue.
        Also includes a starting datetime index and initial capital
        (USD unless otherwise stated).
        Parameters:
        bars - The DataHandler object with current market data.
        events - The Event Queue object.
        window - The start date (bar) of the portfolio.
        initial_capital - The starting capital in USD.
        """ 

        self.bars = bars
        self.events = events
        self.symbol_list = self.bars.symbol_list
        self.start_date = window.start
        self.initial_capital = initial_capital

        self.all_positions = self.define_all_positions()
        self.current_positions = {symbol: 0 for symbol in self.symbol_list}
        self.all_holdings = self.define_all_holdings()
        self.current_holdings = self.define_current_holdings()

    def define_all_positions(self):
        """
        Creates the positions list using the start_date
        to determine when the time index will begin.
        """
        positions = {symbol: 0 for symbol in self.symbol_list}
        positions["datetime"] = self.start_date
        return [positions]

    def define_all_holdings(self):
        """
        Creates the holdings list using the start_date
        to determine when the time index will begin.
        """
        holdings = {symbol: 0.0 for symbol in self.symbol_list}
        holdings["datetime"] = self.start_date
        holdings["cash"] = self.initial_capital
        holdings["commission"] = 0.0
        holdings["total"] = self.initial_capital
        return [holdings]

    def define_current_holdings(self):
        """
        This builds the dictionary which will hold the instantaneous
        value of the portfolio across all symbols.
        """
        holdings = {symbol: 0.0 for symbol in self.symbol_list}
        holdings["cash"] = self.initial_capital
        holdings["commission"] = 0.0
        holdings["total"] = self.initial_capital
        return holdings



    def update_timeindex(self, event):
        """
        Adds a new record to the positions matrix for the current
        market data bar. This reflects the PREVIOUS bar, i.e. all
        current market data at this stage is known (OHLCV).
        Makes use of a MarketEvent from the events queue.
        """
        latest_datetime = self.bars.get_latest_bar_datetime(self.symbol_list[0])

        # Update positions
        # ================
        # Dictionary comprehension list with all symbol keys updated by current_positions values
        positions = {symbol: self.current_positions[symbol] for symbol in self.symbol_list}
        positions["datetime"] = latest_datetime
        # Append the current positions
        self.all_positions.append(positions)

        # Update holdings
        # ===============
        holdings = {symbol: 0.0 for symbol in self.symbol_list}
        holdings["datetime"] = latest_datetime
        holdings["cash"] = self.current_holdings["cash"]
        holdings["commission"] = self.current_holdings["commission"]
        holdings["total"] = self.current_holdings["cash"]
        
        for symbol in self.symbol_list:
            # Approximation to the real value
            market_value = self.current_positions[symbol] * self.bars.get_latest_bar_value(symbol, "close")
            holdings[symbol] = market_value
            holdings["total"] += market_value

        # Append the current holdings
        self.all_holdings.append(holdings)

    def update_positions_after_fill(self, fill):
        """
        Takes a Fill object and updates the position matrix to
        reflect the new position.
        
        Parameters:
        fill - The Fill object to update the positions with.
        """
        # Check whether the fill is a buy or sell
        fill_dir = 0
        if fill.direction == "BUY":
            fill_dir = 1
        if fill.direction == "SELL":
            fill_dir = -1
        # Update positions list with new quantities
        self.current_positions[fill.symbol] += fill_dir*fill.quantity        


    def update_holdings_after_fill(self, fill):
        """
        Takes a Fill object and updates the holdings matrix to
        reflect the holdings value.
        
        Parameters:
        fill - The Fill object to update the holdings with.
        """
        # Check whether the fill is a buy or sell
        fill_dir = 0
        if fill.direction == "BUY":
            fill_dir = 1
        if fill.direction == "SELL":
            fill_dir = -1
        # Update holdings list with new quantities
        fill_cost = self.bars.get_latest_bar_value(fill.symbol, "close") # unknown so set to the market price
        cost = fill_dir * fill_cost * fill.quantity
        self.current_holdings[fill.symbol] += cost
        self.current_holdings["commission"] += fill.commission
        self.current_holdings["cash"] -= (cost + fill.commission)
        self.current_holdings["total"] -= (cost + fill.commission)

    def update_fill(self, event):
        """
        Updates the portfolio current positions and holdings
        from a FillEvent.
        """
        if isinstance(event, FillEvent):
            self.update_positions_after_fill(event)
            self.update_holdings_after_fill(event)

    def generate_naive_order(self, signal):
        """
        Simply files an Order object as a constant quantity
        sizing of the signal object, without risk management or
        position sizing considerations.
        
        Parameters:
        signal - The tuple containing Signal information.
        """
        order = None
        symbol = signal.symbol
        direction = signal.signal_type
        strength = signal.strength
        
        mkt_quantity = floor(100 * strength)
        current_quantity = self.current_positions[symbol]
        order_type = "MKT"
        
        if direction == "LONG" and current_quantity == 0:
            order = OrderEvent(symbol, order_type, mkt_quantity, "BUY")
        if direction == "SHORT" and current_quantity == 0:
            order = OrderEvent(symbol, order_type, mkt_quantity, "SELL")
        if direction == "EXIT" and current_quantity > 0:
            order = OrderEvent(symbol, order_type, abs(current_quantity), "SELL")
        if direction == "EXIT" and current_quantity < 0:
            order = OrderEvent(symbol, order_type, abs(current_quantity), "BUY")
        return order

    def update_signal(self, event):
        """
        Acts on a SignalEvent to generate new orders
        based on the portfolio logic.
        """
        if isinstance(event, SignalEvent):
            order_event = self.generate_naive_order(event)
            self.events.put(order_event)

    def create_equity_curve_dataframe(self):
        """
        Creates a pandas DataFrame from the all_holdings
        list of dictionaries.
        """
        equity_curve = pd.DataFrame(self.all_holdings)
        equity_curve.set_index("datetime", inplace=True)
        equity_curve["returns"] = equity_curve["total"].pct_change()
        equity_curve["equity_curve"] = (1.0 + equity_curve["returns"]).cumprod()
        self.equity_curve = equity_curve


    def output_summary_stats(self):
        """
        Creates a list of summary statistics for the portfolio.
        """
        total_return = self.equity_curve["equity_curve"][-1]
        returns = self.equity_curve["returns"]
        pnl = self.equity_curve["equity_curve"]
        sharpe_ratio = create_sharpe_ratio(returns, periods=252*60*6.5)
        drawdown, max_dd, max_dd_duration = create_drawdowns(pnl)
        self.equity_curve["drawdown"] = drawdown
        
        stats = [("Total Return", "%0.2f%%" % ((total_return - 1.0) * 100.0)),
        ("Sharpe Ratio", "%0.2f" % sharpe_ratio),
        ("Max Drawdown", "%0.2f%%" % (max_dd * 100.0)),
        ("Max Drawdown Duration", "%d" % max_dd_duration)]
        self.equity_curve.to_csv("equity.csv")
        return stats