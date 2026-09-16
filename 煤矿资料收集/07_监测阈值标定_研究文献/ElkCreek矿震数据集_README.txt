# ElkCreek

This repo houses the data and code that accompany the journal article titled "Coal Bursting at the Elk Creek Mine: A Western United States Case Study": 

Boltz MS, Chambers DJA, Larson MK, Lawson HE, Törnman W \[2026]. Coal Bursting at the Elk Creek Mine: A Western United States Case Study. Mining, Metallurgy & Exploration, https://doi.org/10.1007/s42461-026-01497-0.

---

To reproduce the figures from the paper, [install uv](https://docs.astral.sh/uv/getting-started/installation/) and, while in the elkcreek directory run:

```bash
uv run make.py
```

make.py runs all of the scripts that make up the data processing. The scripts are numbered alphanumerically in the order that they should be run in. Output data are written data/derived or and plots are output to the plots folder. All outputs are prefixed with the same alphanumeric identifier as the script used to generate them.

Details about the data itself can be found [here](https://github.com/niosh-mining/elkcreek/tree/main/data/raw).

Scripts starting with "a" relate to seismic event catalog generation.
- a010_combine_catalogs.py combines the original (JMTS) and BEMIS catalogs, giving preference to the BEMIS catalog.
- a020_filter_events.py applies quality control filtering to the catalog.
- a030_add_local_info.py adds the event time in local time and the event latitude and longitude.
- a040_add_geometry.py applies some spatial filters based on which panel or which burst an event is related to.
- a050_add_longwall_info.py adds the longwall face position at the time of the event.

Scripts starting with "b" relate to the underground instrumentation.
 - b010_extract_bpc_data.py grabs the relevant borehole pressure cell data and prepares it for further analysis.
 - b020_extract_support_can.py grabs the relevant support Can displacement data and prepares it for further analysis.

Scripts starting with "c" relate to preparing the station inventory.
 - c010_add_lat_lon_to_stations.py converts the station coordinates from mine coordinates to latitude and longitude (needed for Grond).

Scripts starting with "d" relate to moment tensor inversion.
 - d010_make_pyrocko_catalog.py converts the listing of burst events into a format that is compatible with Pyrocko.
 - d020_make_pyrocko_inv.py converts the station inventory into a format that is compatible with Pyrocko.
 - d025_make_station_resp.py attaches instrument response information to the station inventory.
 - d030_make_pyrocko_picks.py converts the pick times for the burst events to a format that is compatible with Pyrocko.
 - d040_make_displacement_seismograms.py converts the waveforms for the burst events from velocity (m/s) to displacement (m).
 - d050_make_ahfullgreen.py creates a Green's function store for the network.
 - d060_make_grond_config.py creates configuration files to run Grond for Event 2.
 - d070_run_grond.py computes the moment tensor for Event 2. Note that Grond uses Bayesian estimation and will produce a slightly different result each time.

Scripts starting with "p" generate the different plots/figures for the associated paper.
 - p010_mine_maps.py generates the main event and instrumentation maps.
 - p020_magnitudes.py generates magnitude histograms.
 - p030_dot_map.py creates a plot showing the locations of all of the events in the final catalog.
 - p040_spatial_event_count.py creates a plot showing a spatial count of all of the events in the final catalog.
 - p050_plot_aftershocks.py computes and plots the Omori's law for Events 2, 3, and 4.
 - p060_e2_panel1_seismic_progression.py computes the plot examining the evolution of seismicity on Panel 1 leading up to Event 2.
 - p070_e2_panel2_seismicity.py plots the seismicity on Panel 2 leading up to Event 2.
 - p080_mt_decomp_and_plot.py computes the crack closure/double-couple components of the moment tensor for Event 2 and generates relevant moment tensor plots.
 - p090_plot_event_2_instrumentation_response.py creates plots of the in-mine instrumentation's response to Event 2.

The waveform data for all of the events can be found [here](https://data.cdc.gov/browse?q=elk+creek+seismic+waveforms&sortBy=alpha&pageSize=20), but is not necessary to run the scripts.
