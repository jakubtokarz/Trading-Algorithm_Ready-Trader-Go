// Copyright 2021 Optiver Asia Pacific Pty. Ltd.
//
// This file is part of Ready Trader Go.
//
//     Ready Trader Go is free software: you can redistribute it and/or
//     modify it under the terms of the GNU Affero General Public License
//     as published by the Free Software Foundation, either version 3 of
//     the License, or (at your option) any later version.
//
//     Ready Trader Go is distributed in the hope that it will be useful,
//     but WITHOUT ANY WARRANTY; without even the implied warranty of
//     MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
//     GNU Affero General Public License for more details.
//
//     You should have received a copy of the GNU Affero General Public
//     License along with Ready Trader Go.  If not, see
//     <https://www.gnu.org/licenses/>.
#include <array>
#include <iostream>
#include <cmath>

#include <boost/asio/io_context.hpp>

#include <ready_trader_go/logging.h>

#include "autotrader.h"

using namespace ReadyTraderGo;

RTG_INLINE_GLOBAL_LOGGER_WITH_CHANNEL(LG_AT, "AUTO")

constexpr int LOT_SIZE = 25;
constexpr int POSITION_LIMIT = 100;
constexpr int TICK_SIZE_IN_CENTS = 100;
constexpr int MIN_BID_NEARST_TICK = (MINIMUM_BID + TICK_SIZE_IN_CENTS) / TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS;
constexpr int MAX_ASK_NEAREST_TICK = MAXIMUM_ASK / TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS;

constexpr float DS = 0.04f;

AutoTrader::AutoTrader(boost::asio::io_context &context) : BaseAutoTrader(context)
{
    mBidTimes = {};
    mAskTimes = {};
    mSpread = 8;
}

void AutoTrader::DisconnectHandler()
{
    BaseAutoTrader::DisconnectHandler();
    RLOG(LG_AT, LogLevel::LL_INFO) << "execution connection lost";
}

void AutoTrader::ErrorMessageHandler(unsigned long clientOrderId,
                                     const std::string &errorMessage)
{
    RLOG(LG_AT, LogLevel::LL_INFO) << "error with order " << clientOrderId << ": " << errorMessage;
    if (clientOrderId != 0 && ((mAsks.count(clientOrderId) == 1) || (mBids.count(clientOrderId) == 1)))
    {
        OrderStatusMessageHandler(clientOrderId, 0, 0, 0);
    }
}

void AutoTrader::HedgeFilledMessageHandler(unsigned long clientOrderId,
                                           unsigned long price,
                                           unsigned long volume)
{
    RLOG(LG_AT, LogLevel::LL_INFO) << "hedge order " << clientOrderId << " filled for " << volume
                                   << " lots at $" << price << " average price in cents";
}

void AutoTrader::OrderBookMessageHandler(Instrument instrument,
                                         unsigned long sequenceNumber,
                                         const std::array<unsigned long, TOP_LEVEL_COUNT> &askPrices,
                                         const std::array<unsigned long, TOP_LEVEL_COUNT> &askVolumes,
                                         const std::array<unsigned long, TOP_LEVEL_COUNT> &bidPrices,
                                         const std::array<unsigned long, TOP_LEVEL_COUNT> &bidVolumes)
{
    RLOG(LG_AT, LogLevel::LL_INFO) << "order book received for " << instrument << " instrument"
                                   << ": ask prices: " << askPrices[0]
                                   << "; ask volumes: " << askVolumes[0]
                                   << "; bid prices: " << bidPrices[0]
                                   << "; bid volumes: " << bidVolumes[0];

    if (instrument == Instrument::FUTURE)
    {
        std::copy(askPrices.begin(), askPrices.end(), mFutureAskPrices.begin());
        std::copy(bidPrices.begin(), bidPrices.end(), mFutureBidPrices.begin());
    }
    else if (instrument == Instrument::ETF)
    {
        std::copy(askPrices.begin(), askPrices.end(), mEtfAskPrices.begin());
        std::copy(bidPrices.begin(), bidPrices.end(), mEtfBidPrices.begin());
    }
    
    mTime++;

    if (mEtfAskPrices.size() == 0 || mFutureAskPrices.size() == 0)
        return;
    
    unsigned long currentBidPrice = mFutureAskPrices.at(0) + TICK_SIZE_IN_CENTS;
    while (true)
    {
        float mult = currentBidPrice >= mEtfAskPrices.at(0) ? 1.0002f : 0.9999f;
        if (static_cast<float>(currentBidPrice) * mult < static_cast<float>(mFutureBidPrices.at(0)))
            break;
        currentBidPrice -= static_cast<unsigned long>(TICK_SIZE_IN_CENTS);
    }

    unsigned long currentAskPrice = mFutureBidPrices.at(0) - TICK_SIZE_IN_CENTS;
    while (true)
    {
        float mult = currentAskPrice <= mEtfBidPrices.at(0) ? 0.9998f : 1.0001f;
        if (static_cast<float>(currentAskPrice) * mult > static_cast<float>(mFutureAskPrices.at(0)))
            break;
        currentAskPrice += static_cast<unsigned long>(TICK_SIZE_IN_CENTS);
    }

    unsigned long spread = static_cast<unsigned long>(round(mSpread));
    // std::cout << mSpread << " " << spread <<  "\n";
    spread = 4;
    int c = 0;
    while (currentAskPrice - currentBidPrice < spread * TICK_SIZE_IN_CENTS)
    {
        if (c % 2 == 0)
            currentBidPrice -= static_cast<unsigned long>(TICK_SIZE_IN_CENTS);
        else
            currentAskPrice += static_cast<unsigned long>(TICK_SIZE_IN_CENTS);
        c++;
    }
    
    if (currentBidPrice > currentAskPrice)
        return;

    constexpr int lifetime = 10;
    if (static_cast<signed long>(mBids.size() * LOT_SIZE) + mPosition + LOT_SIZE <= static_cast<signed long>(POSITION_LIMIT))
    {
        unsigned long id = mNextMessageId++;
        SendInsertOrder(id, Side::BUY, currentBidPrice, LOT_SIZE, Lifespan::GOOD_FOR_DAY);
        mBids.emplace(id);
        mBidTimes[id] = mTime + lifetime;
        mBidPrices[id] = currentBidPrice;
    }
    if (-static_cast<signed long>(mAsks.size() * LOT_SIZE) + mPosition - LOT_SIZE >= -static_cast<signed long>(POSITION_LIMIT))
    {
        unsigned long id = mNextMessageId++;
        SendInsertOrder(id, Side::SELL, currentAskPrice, LOT_SIZE, Lifespan::GOOD_FOR_DAY);
        mAsks.emplace(id);
        mAskTimes[id] = mTime + lifetime;
        mAskPrices[id] = currentAskPrice;
    }

    for (auto id : mBids) {
        if (mBidTimes[id] <= mTime) {
            SendCancelOrder(id);
            mSpread -= DS;
        } else {
            unsigned long bidPrice = mBidPrices[id];
            float mult = bidPrice >= mEtfAskPrices.at(0) ? 1.0002f : 0.9999f;
            if (static_cast<float>(bidPrice) * mult >= static_cast<float>(mFutureBidPrices.at(0))) {
                SendCancelOrder(id);
                mSpread -= DS;
            }
        }
    }
    for (auto id : mAsks) {
        if (mAskTimes[id] <= mTime) {
            SendCancelOrder(id);
            mSpread -= DS;
        } else {
            unsigned long askPrice = mAskPrices[id];
            float mult = askPrice <= mEtfBidPrices.at(0) ? 0.9998f : 1.0001f;
            if (static_cast<float>(askPrice) * mult <= static_cast<float>(mFutureAskPrices.at(0)))
                SendCancelOrder(id);
                mSpread -= DS;
        }
    }
    
    if (mSpread < 1.0f)
        mSpread = 1.0f;
}

void AutoTrader::OrderFilledMessageHandler(unsigned long clientOrderId,
                                           unsigned long price,
                                           unsigned long volume)
{
    RLOG(LG_AT, LogLevel::LL_INFO) << "order " << clientOrderId << " filled for " << volume
                                   << " lots at $" << price << " cents";
    if (mAsks.count(clientOrderId) == 1)
    {
        SendHedgeOrder(mNextMessageId++, Side::BUY, MAX_ASK_NEAREST_TICK, volume);
        mPosition -= (long)volume;
        mSpread += DS * static_cast<float>(volume) * 1.0;
    }
    else if (mBids.count(clientOrderId) == 1)
    {
        SendHedgeOrder(mNextMessageId++, Side::SELL, MIN_BID_NEARST_TICK, volume);
        mPosition += (long)volume;
        mSpread += DS * static_cast<float>(volume) * 1.0;
    }
}

void AutoTrader::OrderStatusMessageHandler(unsigned long clientOrderId,
                                           unsigned long fillVolume,
                                           unsigned long remainingVolume,
                                           signed long fees)
{
    if (remainingVolume == 0)
    {
        mAsks.erase(clientOrderId);
        mBids.erase(clientOrderId);
        mAskTimes.erase(clientOrderId);
        mBidTimes.erase(clientOrderId);
        mAskPrices.erase(clientOrderId);
        mBidPrices.erase(clientOrderId);
    }
}

void AutoTrader::TradeTicksMessageHandler(Instrument instrument,
                                          unsigned long sequenceNumber,
                                          const std::array<unsigned long, TOP_LEVEL_COUNT> &askPrices,
                                          const std::array<unsigned long, TOP_LEVEL_COUNT> &askVolumes,
                                          const std::array<unsigned long, TOP_LEVEL_COUNT> &bidPrices,
                                          const std::array<unsigned long, TOP_LEVEL_COUNT> &bidVolumes)
{
    RLOG(LG_AT, LogLevel::LL_INFO) << "trade ticks received for " << instrument << " instrument"
                                   << ": ask prices: " << askPrices[0]
                                   << "; ask volumes: " << askVolumes[0]
                                   << "; bid prices: " << bidPrices[0]
                                   << "; bid volumes: " << bidVolumes[0];
}
