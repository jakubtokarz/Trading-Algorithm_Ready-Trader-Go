# Copyright 2021 Optiver Asia Pacific Pty. Ltd.
#
# This file is part of Ready Trader Go.
#
#     Ready Trader Go is free software: you can redistribute it and/or
#     modify it under the terms of the GNU Affero General Public License
#     as published by the Free Software Foundation, either version 3 of
#     the License, or (at your option) any later version.
#
#     Ready Trader Go is distributed in the hope that it will be useful,
#     but WITHOUT ANY WARRANTY; without even the implied warranty of
#     MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#     GNU Affero General Public License for more details.
#
#     You should have received a copy of the GNU Affero General Public
#     License along with Ready Trader Go.  If not, see
#     <https://www.gnu.org/licenses/>.
import asyncio
import itertools
import math
import time

from typing import List

import numpy as np

from ready_trader_go import BaseAutoTrader, Instrument, Lifespan, MAXIMUM_ASK, MINIMUM_BID, Side

LOT_SIZE = 25
SPREAD = 1
LIFETIME = 10

POSITION_LIMIT = 100
TICK_SIZE_IN_CENTS = 100
MIN_BID_NEAREST_TICK = (MINIMUM_BID + TICK_SIZE_IN_CENTS) // TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS
MAX_ASK_NEAREST_TICK = MAXIMUM_ASK // TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS


class AutoTrader(BaseAutoTrader):
    """Example Auto-trader.

    When it starts this auto-trader places ten-lot bid and ask orders at the
    current best-bid and best-ask prices respectively. Thereafter, if it has
    a long position (it has bought more lots than it has sold) it reduces its
    bid and ask prices. Conversely, if it has a short position (it has sold
    more lots than it has bought) then it increases its bid and ask prices.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, team_name: str, secret: str):
        """Initialise a new instance of the AutoTrader class."""
        super().__init__(loop, team_name, secret)
        self.order_ids = itertools.count(1)
        self.bids = {}
        self.asks = {}
        self.future_bids = set()
        self.future_asks = set()
        self.ask_id = self.ask_price = self.bid_id = self.bid_price = self.position = 0

        self.etf_ask_prices = self.etf_ask_volumes = self.etf_bid_prices = self.etf_bid_volumes = self.future_ask_prices \
            = self.future_ask_volumes = self.future_bid_prices = self.future_bid_volumes = (0, 0, 0, 0, 0)

        self.last_etf_ask_price = self.last_etf_bid_price = self.last_future_ask_price = self.last_future_bid_price = 0

        self.t = 0

        self.future_position = 0

        self.time_unhedged = time.time()
        self.hedge_time = 10
        self.speed = 10

    def on_error_message(self, client_order_id: int, error_message: bytes) -> None:
        """Called when the exchange detects an error.

        If the error pertains to a particular order, then the client_order_id
        will identify that order, otherwise the client_order_id will be zero.
        """
        self.logger.warning("error with order %d: %s", client_order_id, error_message.decode())
        if client_order_id != 0 and (client_order_id in self.bids or client_order_id in self.asks):
            self.on_order_status_message(client_order_id, 0, 0, 0)

    def on_hedge_filled_message(self, client_order_id: int, price: int, volume: int) -> None:
        """Called when one of your hedge orders is filled.

        The price is the average price at which the order was (partially) filled,
        which may be better than the order's limit price. The volume is
        the number of lots filled at that price.
        """
        if client_order_id in self.future_asks:
            self.future_position -= volume
        if client_order_id in self.future_bids:
            self.future_position += volume

        if np.abs(-self.position - self.future_position) <= 10:
            self.time_unhedged = time.time()
        self.logger.info("received hedge filled for order %d with average price %d and volume %d", client_order_id,
                         price, volume)

    def on_order_book_update_message(self, instrument: int, sequence_number: int, ask_prices: List[int],
                                     ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]) -> None:
        """Called periodically to report the status of an order book.

        The sequence number can be used to detect missed or out-of-order
        messages. The five best available ask (i.e. sell) and bid (i.e. buy)
        prices are reported along with the volume available at each of those
        price levels.
        """

        self.t += 1

        self.logger.info("received order book for instrument %d with sequence number %d", instrument, sequence_number)

        self.cache_prices(instrument, sequence_number, ask_prices, ask_volumes, bid_prices, bid_volumes)

        if self.etf_ask_prices[0] > 0 and self.etf_bid_prices[0] > 0:
            for order in self.bids:
                if self.bids[order][0] <= self.t:
                    self.send_cancel_order(order)
                    continue
                bid_price = self.bids[order][2]
                multiplier = 1.0002 if bid_price >= self.etf_ask_prices[0] else 0.9999
                if bid_price * multiplier >= self.future_bid_prices[0]:
                    self.send_cancel_order(order)

            for order in self.asks:
                if self.asks[order][0] <= self.t:
                    self.send_cancel_order(order)
                    continue
                ask_price = self.asks[order][2]
                multiplier = 0.9998 if ask_price <= self.etf_bid_prices[0] else 1.0001
                if ask_price * multiplier <= self.future_ask_prices[0]:
                    self.send_cancel_order(order)

            current_bid_price = self.future_ask_prices[0] + TICK_SIZE_IN_CENTS
            while True:
                multiplier = 1.0002 if current_bid_price >= self.etf_ask_prices[0] else 0.9999
                if current_bid_price * multiplier < self.future_bid_prices[0]:
                    break
                current_bid_price -= TICK_SIZE_IN_CENTS

            # choose most competitive ask price at which we make a profit
            current_ask_price = self.future_bid_prices[0] - TICK_SIZE_IN_CENTS
            while True:
                multiplier = 0.9998 if current_ask_price <= self.etf_bid_prices[0] else 1.0001
                if current_ask_price * multiplier > self.future_ask_prices[0]:
                    break
                current_ask_price += TICK_SIZE_IN_CENTS

            diff = current_ask_price - current_bid_price
            spread_in_cents = round(SPREAD) * TICK_SIZE_IN_CENTS
            if diff < spread_in_cents:
                change = ((spread_in_cents - diff) // (2 * TICK_SIZE_IN_CENTS)) * TICK_SIZE_IN_CENTS

                current_bid_price -= change
                current_ask_price += change
                if (diff % 2 == 0) ^ (spread_in_cents % 2 == 0):
                    current_bid_price -= TICK_SIZE_IN_CENTS

            if len(self.bids) * LOT_SIZE + self.position + LOT_SIZE <= POSITION_LIMIT:
                self.bid_id = next(self.order_ids)
                self.send_insert_order(self.bid_id, Side.BUY, current_bid_price, LOT_SIZE, Lifespan.GOOD_FOR_DAY)
                self.bids[self.bid_id] = (self.t + LIFETIME, SPREAD, current_bid_price)

            if -len(self.asks) * LOT_SIZE + self.position - LOT_SIZE >= -POSITION_LIMIT:
                self.ask_id = next(self.order_ids)
                self.send_insert_order(self.ask_id, Side.SELL, current_ask_price, LOT_SIZE, Lifespan.GOOD_FOR_DAY)
                self.asks[self.ask_id] = (self.t + LIFETIME, SPREAD, current_ask_price)

            if time.time() - self.time_unhedged > self.hedge_time / self.speed:
                self.time_unhedged = time.time()
                self.logger.info("position: %d", self.position)
                self.logger.info("position future: %d", self.future_position)
                fut_vol = -self.position - self.future_position
                if fut_vol < -10:
                    order_id = next(self.order_ids)
                    self.send_hedge_order(order_id, Side.ASK, MIN_BID_NEAREST_TICK, -fut_vol)
                    self.future_asks.add(order_id)
                if fut_vol > 10:
                    order_id = next(self.order_ids)
                    self.send_hedge_order(order_id, Side.BUY, MAX_ASK_NEAREST_TICK, fut_vol)
                    self.future_bids.add(order_id)

    def cache_prices(self, instrument: int, sequence_number: int, ask_prices: List[int], ask_volumes: List[int],
                     bid_prices: List[int], bid_volumes: List[int]) -> None:
        """
            Update the cached prices based on the instrument they refer to.
        """
        if instrument == Instrument.ETF:
            self.etf_ask_prices = ask_prices
            self.etf_ask_volumes = ask_volumes
            self.etf_bid_prices = bid_prices
            self.etf_bid_volumes = bid_volumes
        elif instrument == Instrument.FUTURE:
            self.future_ask_prices = ask_prices
            self.future_ask_volumes = ask_volumes
            self.future_bid_prices = bid_prices
            self.future_bid_volumes = bid_volumes
        else:
            self.logger.warning("received order book with sequence number %d, for instrument %d", instrument,
                                sequence_number)

    def on_order_filled_message(self, client_order_id: int, price: int, volume: int) -> None:
        """Called when one of your orders is filled, partially or fully.

        The price is the price at which the order was (partially) filled,
        which may be better than the order's limit price. The volume is
        the number of lots filled at that price.
        """
        self.logger.info("received order filled for order %d with price %d and volume %d", client_order_id, price,
                         volume)

        if client_order_id in self.bids:
            self.position += volume
        elif client_order_id in self.asks:
            self.position -= volume

        if np.abs(-self.position - self.future_position) <= 10:
            self.time_unhedged = time.time()

    def on_order_status_message(self, client_order_id: int, fill_volume: int, remaining_volume: int, fees: int) -> None:
        """Called when the status of one of your orders changes.

        The fill_volume is the number of lots already traded, remaining_volume
        is the number of lots yet to be traded and fees is the total fees for
        this order. Remember that you pay fees for being a market taker, but
        you receive fees for being a market maker, so fees can be negative.

        If an order is cancelled its remaining volume will be zero.
        """
        self.logger.info("received order status for order %d with fill volume %d remaining %d and fees %d",
                         client_order_id, fill_volume, remaining_volume, fees)
        if remaining_volume == 0:
            # It could be either a bid or an ask
            self.bids.pop(client_order_id, 0)
            self.asks.pop(client_order_id, 0)

    def on_trade_ticks_message(self, instrument: int, sequence_number: int, ask_prices: List[int],
                               ask_volumes: List[int],
                               bid_prices: List[int], bid_volumes: List[int]) -> None:
        """Called periodically when there is trading activity on the market.

        The five best ask (i.e. sell) and bid (i.e. buy) prices at which there
        has been trading activity are reported along with the aggregated volume
        traded at each of those price levels.

        If there are less than five prices on a side, then zeros will appear at
        the end of both the prices and volumes arrays.
        """

        if instrument == Instrument.ETF:
            if ask_prices[0] != 0:
                self.last_etf_ask_price = ask_prices[0]
            if bid_prices[0] != 0:
                self.last_etf_bid_price = bid_prices[0]
        else:
            if ask_prices[0] != 0:
                self.last_future_ask_price = ask_prices[0]
            if bid_prices[0] != 0:
                self.last_future_bid_price = bid_prices[0]

        self.logger.info("received trade ticks for instrument %d with sequence number %d", instrument, sequence_number)