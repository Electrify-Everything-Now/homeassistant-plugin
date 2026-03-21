# Anode - Home Assistant Integration

Home Assistant custom integration for Anode systems.

## Features

- Real-time monitoring of hub, batteries, and meters
- Mode control with override capability
- Automatic device discovery
- Scheduled mode management
- House power calculation
- Binary sensors for online status and override detection

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Go to "Integrations"
3. Click the three dots menu and select "Custom repositories"
4. Add this repository URL
5. Click "Install"
6. Restart Home Assistant

### Manual Installation

1. Copy the `custom_components/anode_battery` directory to your Home Assistant `config/custom_components/` directory
2. Restart Home Assistant

## Configuration

1. Go to Settings → Devices & Services
2. Click "Add Integration"
3. Search for "Anode"
4. Enter your credentials:
   - Email
   - API Key
   - Hub ID (friendly ID from the sticker on your hub)

## Entities Created

### Hub Entities
- **Mode** - Current operating mode
- **Version** - Firmware version
- **Uptime** - Hub uptime
- **Next Mode** - Next scheduled mode
- **Next Mode Time** - When the next mode will activate
- **Online** - Hub connectivity status
- **Override Active** - Whether an override is active
- **House Power** - Total house consumption (when EXT_INVERTER present)

### Per-Battery Entities
- **Power** - Battery power (negative = discharging)
- **State of Charge** - Battery SOC percentage
- **Version** - Firmware version
- **Uptime** - Battery uptime
- **Online** - Battery connectivity status

### Per-Meter Entities
- **Power** - Meter power reading
- **Type** - Meter type (PRIMARY, LOAD, MONITOR, EXT_INVERTER)
- **Version** - Firmware version
- **Uptime** - Meter uptime
- **Online** - Meter connectivity status
- **Parent Meter** - Parent meter ID (if applicable)

### Mode Override Controls
- **Charge Override** - Override to charge mode
- **Discharge Override** - Override to discharge mode
- **Idle Override** - Override to idle mode
- **Match Override** - Override to match mode
- **Cancel Override** - Cancel any active override

## Development

### Running Tests

Install test requirements:
```bash
pip install -r requirements_test.txt
```

Run tests:
```bash
pytest
```

Run tests with coverage:
```bash
pytest --cov=custom_components.anode_battery --cov-report=html
```

### Test Structure

- `tests/conftest.py` - Fixtures and mocks
- `tests/test_init.py` - Integration setup/teardown tests
- `tests/test_config_flow.py` - Configuration flow tests
- `tests/test_coordinator.py` - Data coordinator tests
- `tests/test_sensor.py` - Sensor entity tests
- `tests/test_binary_sensor.py` - Binary sensor tests

## API

The integration uses the Anode API endpoints:
- `GET /api/device/status/:id` - Hub and device status
- `GET /api/device/battery/:id` - Battery details
- `GET /api/device/meter/:id` - Meter details
- `GET /api/device/:id/mode` - Current mode
- `GET /api/device/schedule/:id` - Schedule
- `PUT /api/device/:id/override` - Set mode override

## Support

For issues and feature requests, please use the GitHub issue tracker.

## License

MIT License
