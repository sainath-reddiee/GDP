"""Registers the GDP listener with Airflow. Place this file and the gdp_listener folder at the root of plugins.zip."""

from airflow.plugins_manager import AirflowPlugin

from gdp_listener import listener


class GdpListenerPlugin(AirflowPlugin):
    name = "gdp_listener"
    listeners = [listener]
