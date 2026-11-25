# Express One Parcel Tracker

[![Release](https://img.shields.io/github/v/release/ha-parcel-integrations/ha-express-one.svg)](https://github.com/ha-parcel-integrations/ha-express-one/releases)
[![Downloads](https://img.shields.io/github/downloads/ha-parcel-integrations/ha-express-one/total.svg)](https://github.com/ha-parcel-integrations/ha-express-one/releases)
[![Marketplace](https://img.shields.io/badge/Marketplace-Custom-41BDF5.svg)](https://hacs.xyz)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> 📦 Part of [**ha-parcel-integrations**](https://ha-parcel-integrations.github.io/), a family of package-tracking integrations for Home Assistant that all publish the same parcel format, statuses and events, so they plug into cross-carrier automations and the optional [Parcel Aggregator](https://github.com/ha-parcel-integrations/ha-parcel-aggregator). Browse the other carriers on the website.
>
> 💬 Questions or feedback? Join the discussion on the [Home Assistant community](https://community.home-assistant.io/t/packages-postnl-dhl-nl-dpd-and-gls-parcel-integration/112433/).

A custom Home Assistant integration that tracks your [Express One](https://www.expressone.hu) parcels in Croatia and Bulgaria. It reads Express One's public tracking pages, so no account is needed — you enter the tracking number yourself, just like on the Express One website.

> **Pre-release.** No Croatian parcel has been seen yet, and the wording for returned parcels is unknown. Anything the integration cannot place is logged once as a warning with a link to report it, see [Troubleshooting](#troubleshooting). Slovenia is not supported yet.

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Options](#options)
- [Removal](#removal)
- [Sensors](#sensors)
- [Parcel status reference](#parcel-status-reference)
- [Events](#events)
- [Services](#services)
- [Examples](#examples)
- [Debugging](#debugging)
- [Troubleshooting](#troubleshooting)
- [Related integrations](#related-integrations)
- [Disclaimer](#disclaimer)
- [Contributing](#contributing)
- [License](#license)

## Features

- Track any number of Express One parcels by tracking number — no account needed
- One entry per country, Croatia or Bulgaria; a Bulgarian entry asks the Bulgarian tracker first, which gives local Sofia times and the pickup office, and falls back to the Hungarian-side tracker for parcels that have not reached Bulgaria yet
- Bulgarian entries name the pickup office of a parcel waiting at one (`pickup_point`, e.g. `PUP 7005, RUSE`); Croatian entries do not, as that tracker does not show it
- Per-parcel sensor with the canonical status (`registered` / `in_transit` / `out_for_delivery` / `delivered` / …), the carrier's own status text and a tracking deep-link
- Summary sensors: incoming parcels, parcels on their way to or waiting at a pickup point, recently delivered parcels
- `express_one.track_parcel` / `express_one.untrack_parcel` services, so a dashboard button can add a parcel
- A `contents` field on every parcel, set in your own words with the `express_one.update_contents` service
- Events + device triggers for no-code automations (parcel registered, status changed, delivered)
- Opt-in per-parcel status history, with the scan time and wording of every event
- Manual refresh button and a diagnostic last-update sensor

## Requirements

- Home Assistant 2025.12 or newer
- An Express One parcel in Croatia or Bulgaria and its tracking number — no
  account needed. Express One shows no delivery time, weight or sender, so the
  integration has none of those either.

## Installation

### Marketplace (recommended)

Click the button below to open this repository in the Marketplace, then install **Express One**:

[![Open your Home Assistant instance and open a repository inside the Marketplace.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ha-parcel-integrations&repository=ha-express-one&category=integration)

Or add it by hand: in the Marketplace, choose the three-dot menu → **Custom repositories**, add `https://github.com/ha-parcel-integrations/ha-express-one` as an **Integration** and install **Express One**.

Restart Home Assistant if it asks you to.

### Manual

Copy `custom_components/express_one` into your `config/custom_components/` folder and restart Home Assistant.

## Configuration

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=express_one)

Add the integration via **Settings → Devices & Services → Add Integration → Express One** and pick the country the parcels are for. Add the integration a second time for the other country if you need both; each country is its own entry.

| Country | Tracking numbers |
|---|---|
| Bulgaria | 24 digits (usually starting `1582788`), 13 digits, or the 8-digit bill of lading. The Bulgarian tracker is asked first; the Hungarian-side one only when the Bulgarian one does not know the number |
| Croatia | 24 digits (usually starting `1582788`) or 13 digits, read from the Hungarian-side tracker only |
| Slovenia | listed, but setup stops with a notice: Express One Slovenia is not supported yet. We are looking for a real Slovenian parcel to find out which tracker it uses, see the linked issue. |

Then add parcels via the integration's **Configure** dialog, the [`express_one.track_parcel`](#services) service, or a [dashboard button](examples/dashboards/add_parcel_card.yaml). Spaces in a number are ignored.

## Options

Open **Configure** on the integration entry:

| Section | Option | Default | Description |
|---|---|---|---|
| Parcels | Add / remove | — | Manage the tracked tracking numbers. Changes apply immediately, no restart. |
| Delivered parcels | Filter by / amount | last 7 days | How long delivered parcels stay visible on the delivered sensor. |
| Parcel history | Include status history | off | Adds a `history` attribute per parcel with each status update. |

Polling isn't one of these settings: the integration polls on a dynamic,
status-driven schedule with nothing to configure.

## Dynamic polling

Polling isn't a setting here — the integration adjusts its own cadence to
what your tracked parcels are actually doing:

- **Quiet hours** — no polling between 00:00–06:00 local time, aside from one
  catch-up check at each end of that window (around midnight and around 6
  AM), so an overnight update is never missed.
- **Hot (every 15 minutes)** — while any tracked parcel is out for delivery.
  Express One shows no delivery window, so the hot cadence starts as soon as
  the parcel is out for delivery.
- **Normal (every 45 minutes)** — for anything else still on its way.
- **Fully paused** — once every tracked parcel has been delivered, or nothing
  is tracked at all, polling stops until you add a parcel back (adding one
  always triggers an immediate check, regardless of the pause).
- A small, fixed per-hub offset is added on top, so not every Express One
  hub out there polls at exactly the same second.

## Removal

Standard HA removal applies: **Settings → Devices & Services → Express One → ⋮ → Delete**. Nothing is stored on Express One's side.

## Sensors

Entity ids carry the entry's name, e.g. `sensor.express_one_croatia_…` or `sensor.express_one_bulgaria_…`; the table shows them without it.

| Entity | Description |
|---|---|
| `sensor.express_one_incoming_parcels` | Number of active tracked parcels, full list under the `parcels` attribute |
| `sensor.express_one_parcel_<code>` | One per tracked parcel; state is the canonical status, attributes carry the full normalised parcel |
| `sensor.express_one_next_delivery` | Stays empty: Express One shows no delivery time |
| `sensor.express_one_en_route_to_pickup_point` | Incoming parcels on their way to a pickup point that have not arrived yet |
| `sensor.express_one_awaiting_pickup` | Incoming parcels that are ready to collect at a pickup point |
| `sensor.express_one_delivered_parcels` | Recently delivered parcels (see the retention option) |
| `calendar.express_one_deliveries` | Read-only calendar of expected deliveries; stays empty as Express One shows no delivery time |
| `sensor.express_one_last_successful_update` | Diagnostic: when Express One was last polled successfully |

A delivered parcel moves from its per-parcel sensor to the delivered sensor automatically.

## Parcel status reference

The `status` field is the carrier-agnostic enum shared by the whole integration family:

| Status | Meaning |
|---|---|
| `registered` | Announced / received by Express One |
| `in_transit` | In the sorting network |
| `out_for_delivery` | With the courier today |
| `at_pickup_point` | Waiting for you at a pickup location |
| `delivered` | Delivered |
| `problem` | A delivery attempt failed; the next scan replaces it |
| `unknown` | Not found yet, or a scan we have not mapped yet |

The carrier's own wording is always available as `raw_status`. `returning` is not used: the wording for a returned parcel has not been seen yet.

## Events

The integration fires these on the event bus (also available as device triggers on the Express One device):

| Event | When |
|---|---|
| `express_one_parcel_registered` | A new parcel appears in the active list |
| `express_one_parcel_status_changed` | A parcel's canonical status changes (`old_status` / `new_status` in the payload), except the final hop to delivered |
| `express_one_parcel_delivered` | A parcel is delivered |
| `express_one_parcel_delivery_time_changed` | Never fires: Express One shows no delivery time |

Every payload is the full normalised parcel plus the hub's `device_id`. Events are suppressed on the first refresh after start-up.

## Services

| Service | Fields | Description |
|---|---|---|
| `express_one.track_parcel` | `tracking_code`, `country` (optional), `contents` (optional) | Start tracking a parcel, optionally noting what is in it. `country` (`HR` or `BG`) is only needed when both entries are set up |
| `express_one.untrack_parcel` | `tracking_code` | Stop tracking a parcel, in whichever entry follows it |
| `express_one.update_contents` | `tracking_code`, `contents`, `config_entry_id` (optional) | Set what is in a parcel, tracked or not; an empty `contents` clears it. It goes to every entry that follows the code, or to the one named by `config_entry_id`; with both countries set up, a code no entry follows is refused unless you name the entry |

## Examples

Ready-to-paste automations and dashboard snippets live in [`examples/`](examples/), including tracking a new parcel straight from a dashboard.

### Community Lovelace cards

Third-party cards that work with this integration's sensors:

- [jonisnet/ha-parcel-card](https://github.com/jonisnet/ha-parcel-card)
- [klaptafel/ha-package-tracker-card](https://github.com/klaptafel/ha-package-tracker-card)

## Debugging

```yaml
logger:
  logs:
    custom_components.express_one: debug
```

## Troubleshooting

- **A parcel shows `unknown`** — Express One does not know the number yet (not registered by the sender, or older than 180 days), or the number is wrong. A parcel that was seen before keeps its last status. It picks up automatically once Express One has it.
- **A Bulgarian bill of lading shows `unknown` in a Croatia entry** — the 8-digit number exists only on the Bulgarian tracker; add it to a Bulgaria entry.
- **A warning mentions an unrecognised scan, page or time** — please [open an issue](https://github.com/ha-parcel-integrations/ha-express-one/issues/new?template=unrecognised_status.yml) with the logged line so the mapping can be extended. Croatian parcels and returned parcels are the least confirmed.
- **Times look an hour off** — scan times have no time zone on the tracker pages. They are read as Budapest time on the Hungarian-side tracker and Sofia time on the Bulgarian one, so a Bulgarian scan that only the Hungarian-side tracker shows reads an hour early.

## Related integrations

This integration is part of [**ha-parcel-integrations**](https://ha-parcel-integrations.github.io/) — a family of
parcel-carrier integrations that all publish the same canonical parcel format,
statuses and events.

- [**Parcel Aggregator**](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) rolls every installed carrier
  up into one set of sensors.
- Browse [the organisation](https://ha-parcel-integrations.github.io/) for the current list of supported carriers.

## Disclaimer

This is an independent, community-built project. It is not affiliated with, endorsed by, sponsored by, or supported by Express One, Home Assistant, or any other third party referenced in this project. Please don't contact Express One for support with this integration.

All third-party trademarks, trade names, product names, logos, and other brand assets are the property of their respective owners. References to them are solely to identify the relevant carrier or service and do not imply affiliation, sponsorship, or endorsement. Nothing in this project grants or implies any licence or right to use third-party brand assets.

This integration may rely on public, unofficial, or undocumented carrier interfaces, accessed without an account. These may change or be withdrawn without notice and may be subject to Express One's terms. Data is sent only to Express One's own services or those of its group; this project operates no servers of its own. You are responsible for ensuring that your use complies with applicable law and those terms. Use is at your own risk; see the [licence](LICENSE) for warranty limitations.

This integration reads the same public tracking pages as the Express One websites (`tracking.expressone.hu` and `expressone.bg`). It has no access to anything behind a login.

## Contributing

Pull requests and issues are welcome. Please open an issue before
submitting a large change.

## License

[MIT](LICENSE)
