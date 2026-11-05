from pathlib import Path

import pandas as pd
import streamlit as st

from alerts import detect_ndvi_drop
from storage import connect, get_observations, get_plot_names


ROOT = Path(__file__).resolve().parent
st.set_page_config(page_title="Farm Monitoring", layout="wide")
st.title("Farm Monitoring Dashboard")
st.caption("Satellite index history and review signals for monitored plots.")

connection = connect()
try:
    plot_names = get_plot_names(connection)
    if not plot_names:
        st.info("No results yet. Run `python pipeline.py` to process a satellite image.")
        st.stop()

    selected_plot = st.sidebar.selectbox("Farm plot", plot_names)
    threshold = st.sidebar.slider(
        "NDVI decrease alert threshold",
        min_value=0.05,
        max_value=0.50,
        value=0.15,
        step=0.05,
        help="Absolute NDVI decrease from the recent baseline mean.",
    )
    baseline_days = st.sidebar.slider(
        "Baseline period (days)", min_value=14, max_value=90, value=30, step=1
    )

    plot_rows = get_observations(connection, [selected_plot])
    plot_data = pd.DataFrame(plot_rows).sort_values(["image_date", "created_at"])
    latest = plot_data.iloc[-1]

    st.subheader(selected_plot)
    left, middle, right = st.columns(3)
    left.metric("Latest image", latest["image_date"])
    middle.metric("Satellite", latest["satellite"])
    right.metric("Telegram delivery", latest["delivery_status"])

    alert = detect_ndvi_drop(
        plot_rows, threshold=threshold, baseline_days=baseline_days
    )
    if alert is None:
        st.info(
            "Not enough recent NDVI observations for an alert comparison "
            "(at least 3 earlier observations are needed)."
        )
    elif alert["triggered"]:
        st.warning(
            f"Review signal: NDVI is down {alert['drop']:.2f} from the "
            f"{baseline_days}-day baseline mean ({alert['baseline_mean']:.2f}) "
            f"on {alert['latest_date']}. This is a signal to investigate, "
            "not a crop diagnosis."
        )
    else:
        st.success(
            f"No configured NDVI-drop signal. Latest decrease from baseline: "
            f"{alert['drop']:.2f} (threshold {threshold:.2f})."
        )

    available_indices = [
        column
        for column in ("ndvi", "ndmi", "ndwi", "ndre", "evi", "gci")
        if plot_data[column].notna().any()
    ]
    selected_indices = st.multiselect(
        "Indices to chart",
        available_indices,
        default=["ndvi"] if "ndvi" in available_indices else available_indices[:1],
        format_func=str.upper,
    )
    if selected_indices:
        st.subheader("Index history")
        chart_data = (
            plot_data.groupby("image_date", as_index=True)[selected_indices]
            .mean()
            .sort_index()
        )
        st.line_chart(chart_data)
    else:
        st.info("No index values are available for this plot yet.")

    report_path = Path(latest["report_path"])
    if not report_path.is_absolute():
        report_path = ROOT / report_path
    st.subheader("Latest heatmap report")
    if report_path.is_file():
        st.image(str(report_path), caption=f"{selected_plot} — {latest['image_date']}")
    else:
        st.warning(f"Saved heatmap is unavailable at `{report_path}`.")

    with st.expander("Observation history"):
        st.dataframe(
            plot_data[
                [
                    "image_date",
                    "satellite",
                    "ndvi",
                    "ndmi",
                    "ndwi",
                    "ndre",
                    "evi",
                    "gci",
                    "delivery_status",
                    "delivery_error",
                ]
            ],
            width="stretch",
        )

    st.subheader("Compare plots")
    comparison_plots = st.multiselect(
        "Plots to compare by NDVI",
        plot_names,
        default=[selected_plot],
    )
    if comparison_plots:
        comparison_data = pd.DataFrame(
            get_observations(connection, comparison_plots)
        )
        comparison_data = (
            comparison_data.groupby(["image_date", "plot_name"], as_index=False)["ndvi"]
            .mean()
            .pivot(index="image_date", columns="plot_name", values="ndvi")
        )
        st.line_chart(comparison_data)
finally:
    connection.close()
