# -*- coding: utf-8 -*-
import json
import datetime
from flask import (
    Blueprint,
    redirect,
    current_app,
    render_template,
    request,
    url_for,
    flash,
    jsonify,
    session,
)
from flask_user import current_user, login_required

from app.forms import forms
from app.utils import form_utils
from app.models import StrategyModel
from app import task

blueprint = Blueprint("strategy", __name__, url_prefix="/strategy")


@blueprint.route("/_get_exchange_quote_currencies")
def _get_exchange_quote_currencies():
    exchange = request.args.get("exchange")
    quote_currencies = task.get_exchange_quote_currencies(exchange) or []
    quote_choices = []
    for q in quote_currencies:
        # value, display
        quote_choices.append((q, q.upper()))
    return jsonify(quote_choices)


@blueprint.route("/_get_available_asset_pairs")
def _get_available_asset_pairs():
    exchange = request.args.get("exchange")
    quote_currency = request.args.get("quote_currency")
    asset_pairs = task.get_exchange_asset_pairs(exchange) or []
    pair_choices = []

    # asset pairs are tuple of same pair (choice)
    for p in asset_pairs:
        quote = p.split("_")[1]
        if quote.lower() == quote_currency.lower():
            # value, display
            pair_choices.append((p, p.upper()))

    return jsonify(pair_choices)


@blueprint.route("/_get_group_indicators/")
def _get_group_indicators():
    group = request.args.get("group", "01", type=str)
    indicators = task.get_indicators_by_group(group)
    return jsonify(indicators)


@blueprint.route("/_get_indicator_params/")
def _get_indicator_params():
    indicator_abbrev = request.args.get("indicator", "01", type=str)
    params_obj = task._get_indicator_params(indicator_abbrev)
    return jsonify(params_obj)


@blueprint.route("/<strat_id>", methods=["GET"])
@login_required
def strategy_status(strat_id):
    strat = StrategyModel.query.filter_by(uuid=strat_id).first_or_404()
    if strat not in current_user.strategies:
        flash("Strategy Not Found in DB", category="error")
        current_app.logger.error(f"Strat {strat_id} not Found")

    if strat.status in ["finished", "failed"]:
        return render_template("strategy/strategy_result.html", strat=strat)

    return render_template("account/strategy_status.html", strat_id=strat_id)


@blueprint.route("backtest/strategy/<strat_id>", methods=["GET"])
def public_backtest_status(strat_id):
    return render_template("account/strategy_status.html", strat_id=strat_id)


@blueprint.route("/build", methods=["GET", "POST"])
def build_strategy():

    form = forms.BasicTradeInfoForm()
    form.asset.choices = []
    form.quote_currency.choices = []
    if form.validate_on_submit():
        live = form.trade_type.data in ["live", "paper"]
        simulate_orders = form.trade_type.data == "paper"

        strat_dict = form_utils.build_strat_dict_from_form(form)
        strat_dict["live"] = live
        strat_dict["simulate_orders"] = simulate_orders
        session["strat_dict"] = strat_dict

        job_id, queue_name = task.queue_strat(
            json.dumps(strat_dict), current_user.id, live, simulate_orders
        )
        return redirect(url_for("strategy.strategy_status", strat_id=job_id))

    return render_template("strategy/trading.html", form=form)


@blueprint.route("/build_advanced", methods=["GET", "POST"])
def build_strategy_advanced():
    task.indicator_group_name_selectors()
    task.all_indicator_selectors()
    form = forms.AdvancedTradeInfoForm()
    # form.base_currency.choices = []
    form.asset.choices = []
    if form.validate_on_submit():

        trading_dict = form_utils.process_trading_form(form)

        live = form.trade_type.data in ["live", "paper"]
        simulate_orders = form.trade_type.data == "paper"

        trading_dict["START"] = datetime.datetime.strftime(
            form.start.data, "%Y-%m-%d %H:%M"
        )
        trading_dict["END"] = datetime.datetime.strftime(
            form.end.data, "%Y-%m-%d %H:%M"
        )

        session["strat_dict"] = {
            "name": form.name.data,
            "trading": trading_dict,
            "live": live,
            "simulate_orders": simulate_orders,
        }

        return redirect(url_for("strategy.build_indicators"))

    return render_template("strategy/trading.html", form=form)


@blueprint.route("build/indicators", methods=["GET", "POST"])
def build_indicators():

    strat_dict = session.get("strat_dict", {})
    if not strat_dict.get("trading", {}):
        return redirect(url_for("strategy.build_strategy"))
    indicator_form = forms.IndicatorInfoForm()
    indicator_form.group.choices = task.indicator_group_name_selectors()
    indicator_form.indicator_name.choices = task.all_indicator_selectors()

    if request.method == "POST" and indicator_form.validate_on_submit():

        indicator_dict = form_utils.process_indicator_form(indicator_form)
        params = {}

        # get params outside of wtf form
        for key in request.form.keys():
            if "param-" in key:
                name, val = key.strip("param-"), request.form.get(key)
                params[name] = val

        indicator_dict["params"] = params

        strat_indicators = session["strat_dict"].get("indicators", [])
        strat_indicators.append(indicator_dict)

        session["strat_dict"]["indicators"] = strat_indicators

        # render new form if adding another
        if indicator_form.add_another.data:
            return render_template("strategy/indicators.html", form=indicator_form)

        return redirect(url_for("strategy.build_signals"))

    return render_template("strategy/indicators.html", form=indicator_form)


@blueprint.route("build/signals", methods=["GET", "POST"])
def build_signals():
    strat_dict = session.get("strat_dict", {})
    if not strat_dict.get("trading", {}):
        return redirect(url_for("strategy.build_strategy"))

    live, simulate_orders = strat_dict["live"], strat_dict["simulate_orders"]
    # get indicators from session
    # to use as param options
    # submit strat if no indicators chosen
    active_indicators = session["strat_dict"].get("indicators")
    if active_indicators is None:
        job_id, queue_name = task.queue_strat(
            json.dumps(strat_dict), current_user.id, live, simulate_orders
        )
        return redirect(url_for("account.strategy_status", strat_id=job_id))

    indicator_choices = [(i["name"], i["name"]) for i in active_indicators]

    signal_form = forms.SignalForm()

    signal_form.target_series.choices = indicator_choices
    signal_form.trigger_series.choices = indicator_choices

    if request.method == "POST":

        # get and setup session signal object
        existing_signals = session["strat_dict"].get("signals", {})
        existing_signals["sell"] = existing_signals.get("sell", [])
        existing_signals["buy"] = existing_signals.get("buy", [])

        signal_dict = form_utils.process_signal_form(signal_form)

        if signal_form.signal_type == "sell":
            existing_signals["sell"].append(signal_dict)
        else:
            existing_signals["buy"].append(signal_dict)

        session["strat_dict"]["signals"] = existing_signals

        # render new form if adding another
        if signal_form.add_another.data:
            return render_template("strategy/signals.html", form=signal_form)

        # remove from session if submitting strat
        strat_dict = session.pop("strat_dict")
        live, simulate_orders = strat_dict["live"], strat_dict["simulate_orders"]

        job_id, queue_name = task.queue_strat(
            json.dumps(strat_dict), current_user.id, live, simulate_orders
        )
        return redirect(url_for("strategy.strategy_status", strat_id=job_id))

    return render_template("strategy/signals.html", form=signal_form)
