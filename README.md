# Anode for Home Assistant

Monitor and control an Anode battery system from Home Assistant: live power and state of charge for every battery and meter, lifetime energy counters for the Energy dashboard, and schedule overrides.

Requires Home Assistant 2025.1 or later and an Anode account with an API key.

## Installation

### HACS

1. In HACS, open the menu (⋮) and choose **Custom repositories**. Add `https://github.com/Electrify-Everything-Now/homeassistant-plugin` with the category **Integration**.
2. Search for **Anode** and install it.
3. Restart Home Assistant.

### Manual

1. Copy `custom_components/anode_battery` into your Home Assistant `config/custom_components/` folder.
2. Restart Home Assistant.

## Setup

1. In the Anode web app, go to **Settings → API** and create an API key.
2. In Home Assistant, go to **Settings → Devices & services → Add integration** and choose **Anode**.
3. Enter your Anode email and the API key.

The integration finds the hub on your account. If the account has no hub of its own, for example an installer account, it asks for the hub ID printed on the hub's label. To add another hub, add the integration again.

### Options

Open **Settings → Devices & services → Anode → Configure**.

| Option | Default | Controls |
| --- | --- | --- |
| Device list and firmware | 120 s | Which batteries and meters are paired, firmware versions, and names from the Anode app |
| Power and energy readings | 30 s | Power, state of charge and energy counters |

Both have a minimum of 10 seconds. Each read is relayed from the Anode cloud to the hub over the internet. The mode and schedule are read every 5 minutes and just after each scheduled change. Battery limits are read every 10 minutes.

Battery BMS readings (pack voltage, temperatures and cell voltages) need a separate request per battery, so they are read every 2 minutes. A battery whose firmware does not report them is checked again every hour.

### Changing credentials

Choose **Reconfigure** from the integration's menu. If Anode stops accepting the API key, Home Assistant asks you to re-authenticate.

## Devices and entities

The integration creates a device for the hub and for each battery and meter. Device names come from the Anode app and update when you rename a device there; a name you set in Home Assistant takes precedence.

Batteries and meters paired later appear automatically. A device the hub no longer reports can be deleted from its device page.

### Hub

| Entity | Description |
| --- | --- |
| Mode | The mode the hub is running: `CHARGE`, `DISCHARGE`, `IDLE` or `MATCH` |
| Next scheduled mode, Next mode change | The next change in the hub's schedule |
| Online | Off when the hub does not respond |
| Override active | On when the running mode differs from the schedule (see [limitations](#known-limitations)) |
| Override mode | Shows the running mode. Choosing a mode overrides the schedule for the **Override duration**. See [Overriding the schedule](#overriding-the-schedule) |
| Override duration | Minutes an override from **Override mode** lasts. Stored in Home Assistant |
| Cancel override | Ends an override early and returns the hub to its schedule |
| Maximum charge power, Maximum discharge power | Fleet power limits in watts. Hubs that store limits as a percent also get **(percent)** versions |
| Grid import energy, Grid export energy | Lifetime totals across grid meters. Created when a grid meter exists |
| House power | Grid power minus generation and battery power. Created when a grid meter exists |
| House energy | Lifetime net house consumption. Created when a grid meter exists |
| Battery charge energy, Battery discharge energy | Lifetime totals across all batteries |
| Grid import energy today, Grid export energy today, House energy today, Battery charge energy today, Battery discharge energy today | Energy since midnight. See [Energy today](#energy-today) |
| Battery energy capacity, Battery energy remaining, Average state of charge | Totals and capacity-weighted average across batteries |
| Firmware version, Uptime | Diagnostic |

### Battery

| Entity | Description |
| --- | --- |
| Power | Positive while charging, negative while discharging |
| Charging power, Discharging power | Power split by direction, always positive |
| State of charge | Percent |
| Capacity, Capacity remaining | Amp-hours |
| Energy capacity, Energy remaining | Watt-hours |
| Power status | For example `CHARGING`, `DISCHARGED` or a fault code |
| Charge energy, Discharge energy | Lifetime counters in kWh |
| Pack voltage | Measured battery voltage |
| Highest temperature | The warmest of the battery's temperature probes |
| Cell voltage difference | Gap between the highest and lowest cell, in mV. A widening gap means the cells are drifting out of balance |
| Lowest temperature, Highest cell voltage, Lowest cell voltage | Disabled by default. Enable them from the entity's settings |
| Temperature *n*, Cell *n* voltage | One per probe and per cell. Disabled by default |
| Minimum state of charge, Maximum state of charge | The battery's operating window |
| Online | Whether the hub reports the battery as connected |
| Nominal voltage, Firmware version, Uptime | Diagnostic |

### Meter

| Entity | Description |
| --- | --- |
| Power | Positive when importing, negative when exporting |
| Import power, Export power | Power split by direction, always positive |
| Import energy, Export energy | Lifetime counters in kWh |
| Voltage, Current, Power factor | Disabled by default. Enable them from the entity's settings |
| Online | Whether the hub reports the meter as connected |
| Meter type, Parent meter, Firmware version, Uptime | Diagnostic |

A meter is treated as a grid meter when its type is `PRIMARY` or its purpose in the Anode app is **Primary**, and as generation when its type is `EXT_INVERTER` or its purpose is **Solar**.

## Energy dashboard

| Energy dashboard setting | Sensor |
| --- | --- |
| Grid consumption | Hub **Grid import energy** |
| Return to grid | Hub **Grid export energy** |
| Solar production | Solar meter **Export energy** |
| Battery: energy going in | Hub **Battery charge energy** |
| Battery: energy coming out | Hub **Battery discharge energy** |

The hub's grid sensors keep working if the grid meter is replaced or a second one is added, so the Energy dashboard does not need changing. The grid meter's own **Import energy** and **Export energy** sensors report the same counters.

Use the lifetime sensors here, not the **… today** sensors: the Energy dashboard calculates daily, weekly and monthly totals itself.

### Energy today

Each hub lifetime total has a **today** version for dashboards and automations: grid import, grid export, house, battery charge and battery discharge.

- They return to 0 at midnight in Home Assistant's time zone.
- Energy used while Home Assistant is stopped still counts, towards the day it starts again.
- If a lifetime total drops, for example after a meter or battery is replaced, today's figure keeps its value and carries on counting.
- They are unavailable while readings cannot be fetched, and pick up where they left off afterwards.

## Overriding the schedule

The hub device has three controls that work together:

| Control | Where | What it does |
| --- | --- | --- |
| **Override duration** | Configuration | How long an override lasts, in minutes. Default 60, up to 7 days |
| **Override mode** | Controls | Choosing a mode runs it for the Override duration, then the hub returns to its schedule |
| **Cancel override** | Controls | Ends an override early |

To charge for two hours:

1. Set **Override duration** to `120`.
2. In **Override mode**, choose **Charge**.

The duration is stored in Home Assistant, not on the hub, and is kept across restarts. It is only used when you choose a mode, so set it first.

### What to expect

- **Override mode always shows the mode the hub is running**, including while it follows its schedule. It is not a record of the last mode you chose.
- **Changing Override duration does not change an override that is already running.** Choose the mode again to restart the override with the new duration.
- **Choosing the mode that is already shown still starts an override.** For example, choosing **Charge** during a scheduled charge slot keeps charging for the full duration, even past the end of the slot.
- **Octopus smart charging is paused during an override.** The hub ignores Octopus dispatches while an override from Home Assistant or the Anode app is running.
- **Home Assistant cannot show when an override will end.** The Anode API does not report it. **Override active** is on while the running mode differs from the schedule, so it stays off for an override that matches the schedule.
- The `anode_battery.set_override` action takes its own `duration` and ignores Override duration. Use the action in automations.

### Dashboard card

The controls sit in different sections of the device page. To keep them together, add a card like this. Entity IDs start with your hub's name, so adjust them to match:

```yaml
type: entities
title: Battery override
entities:
  - entity: sensor.home_hub_mode
  - entity: binary_sensor.home_hub_override_active
  - entity: number.home_hub_override_duration
  - entity: select.home_hub_override_mode
  - entity: button.home_hub_cancel_override
```

## Actions

### Set override (`anode_battery.set_override`)

Runs the hub in a mode for a set time, ignoring its schedule.

| Field | Description |
| --- | --- |
| `device_id` | The hub, or any battery or meter paired to it |
| `mode` | `CHARGE`, `DISCHARGE`, `IDLE` or `MATCH` |
| `duration` | Seconds. `0` ends any active override |

### Cancel override (`anode_battery.cancel_override`)

Returns the hub to its schedule. Takes `device_id`.

Both actions still accept `hub_id` in place of `device_id`, so automations written for version 0.1 keep working.

### Example: charge while electricity is free

```yaml
automation:
  - alias: Charge the battery when the price is negative
    triggers:
      - trigger: numeric_state
        entity_id: sensor.electricity_price
        below: 0
    actions:
      - action: anode_battery.set_override
        data:
          device_id: 5f1c2d3e4a5b6c7d8e9f0a1b2c3d4e5f
          mode: CHARGE
          duration: 1800
```

## Upgrading from 0.1

Entity IDs and history carry over. Changes you may notice:

- **The Charge / Discharge / Idle / Match override selects are removed.** Use **Override mode** and **Override duration** instead. A repair notice lists the ones removed from your system.
- **Energy today** sensors keep today's figure through the upgrade. They no longer lose it when a lifetime total drops or Home Assistant restarts.
- **Minimum and maximum state of charge** now show the hub's values. Previously they stayed unknown.
- **Cancel override** now sends `MATCH` for 0 seconds, which the Anode API documents as returning to the schedule. Previously it sent `IDLE`.
- **House power** is created for any hub with a grid meter, not only hubs with an external inverter.
- **Grid import energy** and **Grid export energy** on the hub now total every grid meter, and never step backwards.
- Meters have new **Voltage**, **Current** and **Power factor** sensors, disabled by default.
- Readings for all batteries and all meters are fetched in two requests per poll instead of one per device. The default reading interval is now 30 seconds (was 10). Intervals you set yourself are kept.
- When a battery or meter stops reporting, its sensors become unavailable instead of showing zero, and hub totals that need it wait for it rather than counting it as zero.
- Batteries without a name in the Anode app are called **Anode Battery *id*** instead of **Anode *id***.

## Known limitations

- **Override active is inferred.** The Anode API does not report overrides, so the integration compares the running mode with the schedule. Octopus smart-charging dispatches also show as overrides.
- The schedule is read in Home Assistant's time zone. If the hub uses a different time zone, **Next mode change** is wrong by the difference.
- Everything goes through the Anode cloud, so the integration needs internet access, and commands take a few seconds to reach the hub.
- **House energy**, **Grid import/export energy** and **Battery charge/discharge energy** on the hub are calculated from lifetime counters. If a calculation comes out lower than before, for example after a meter or battery is replaced, the sensor holds its last value until the calculation catches up rather than going backwards.

## Troubleshooting

- **Download diagnostics** from the hub's device page (⋮ menu) and attach them to an issue. Your email and API key are removed from the file.
- **"The hub did not respond"** means the Anode cloud could not reach the hub. Check its power and internet connection. The hub's **Online** sensor turns off while this lasts.
- To see more detail, enable debug logging:

  ```yaml
  logger:
    logs:
      custom_components.anode_battery: debug
  ```

## Removal

1. Go to **Settings → Devices & services → Anode**, open the menu (⋮) and choose **Delete**.
2. Uninstall from HACS, or delete `custom_components/anode_battery`, then restart Home Assistant.
3. If nothing else uses the API key, delete it in the Anode web app.

## Development

```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements_test.txt
pytest --cov=custom_components.anode_battery --cov-report=term-missing
```

| Path | Contents |
| --- | --- |
| `custom_components/anode_battery/api/` | Async client and typed models for the Anode cloud API. No Home Assistant imports, so it can be published to PyPI as its own package |
| `coordinator.py` | Four coordinators: status, telemetry, mode and schedule, settings |
| `entity.py` | Base entity, dynamic device handling, command error handling |
| `sensor.py`, `binary_sensor.py`, `number.py`, `select.py`, `button.py` | Entity descriptions and platforms |
| `tests/fixtures/` | API responses in the shape the Anode backend returns them |

Tests mock HTTP responses rather than the client, so response parsing is covered too.

### API endpoints used

Paths are relative to `https://api.anode.energy/api` (`API_BASE_URL` in `api/client.py`).

| Method | Path | Used for |
| --- | --- | --- |
| GET | `/user/devices` | Finding the account's hub during setup |
| GET | `/device/status/{hub}` | Paired devices, firmware, connectivity |
| GET | `/user/device-metadata/{hub}` | Names and meter purposes from the Anode app |
| GET | `/device/battery/{hub}` | Readings for all batteries |
| GET | `/device/meter/{hub}` | Readings for all meters |
| GET | `/device/{hub}/mode` | Running mode |
| GET | `/device/schedule/{hub}` | Schedule |
| GET | `/device/config/{hub}/{key}` | `socConfig`, `maxChargePower`, `maxDischargePower` |
| PUT | `/device/config/{hub}` | Changing those settings |
| PUT | `/device/{hub}/override` | Starting and cancelling overrides |

## License

MIT
