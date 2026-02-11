<img src="https://www.skinergy.lol/frag-logo-long-yellow.png" alt="Skinergy Logo" height="150">

> **This repo contains the source code for the Skinergy Desktop Uploader.** You can download the latest stable build from [Releases](https://github.com/kenji-berry/Skinergy/releases) or build it yourself — see [BUILD_PYTHON.md](BUILD_PYTHON.md).

***Skinergy is now in Full Release (v1.0).*** *While the core experience is stable, we are constantly refining features based on community feedback.*

**Skinergy** is a League of Legends **skin collection and comparison app with real-time social features**. 
Players can upload their owned skins, track their collection milestones, and see friends' activity live without refreshing the page. Skinergy blends personal collection tracking with shared social interaction, allowing teams to coordinate their outfits before they even hit the Rift.

---

## Features

- **Skin Collection Management**
  Upload and track your collection using Riot's Data Dragon (ddragon) and integrated API sources. Skinergy provides enhanced metadata and rarity statistics not found in the standard League client.

- **Live Sessions & Skin Matching Algorithm**
  Create or join live sessions with up to five participants. Using WebSockets for real-time synchronization, the host can filter for:
  - **Strict Mode:** Only primary-role champions (e.g., Poppy as a Support).
  - **Flexible Mode:** Includes off-role champions (e.g., Poppy as a Jungler).
  
  Our algorithm analyzes the combined inventories of all participants to suggest matching themes (e.g., a full team of *Infernal* or *Star Guardian* skins), ranked by theme cohesion and user preference. Results are updated instantly as participants change their selections.

- **Secure Skin Uploader (Desktop App)**
  - **Transparency:** Open-source Python/Tkinter app.
  - **Security:** Each upload is tied to a unique account-linked code to prevent unauthorized data spoofing.
  - **Efficiency:** Automatically identifies and adds new skins acquired since your last upload.

---

## Desktop Uploader

The uploader source code lives in this repo. See [BUILD_PYTHON.md](BUILD_PYTHON.md) for build instructions.

| File | Function |
|---|---|
| `get_skins_gui.py` | Main Application (Tkinter GUI) |
| `security_config.py` | API configuration, input validation, and rate limiting |
| `build_exe.py` | PyInstaller build script |
| `requirements-desktop.txt` | Python dependencies |
| `icon.ico` | App icon |

> **Windows Defender warning:** Because the executable is not signed with a paid certificate, Windows SmartScreen may flag it. Click **"More info"** then **"Run anyway"**. You can verify the safety of the tool by reviewing the source code in this repository.

---

## Support & Feedback

If you encounter bugs, performance issues, or have suggestions for new features, please reach out!

* **Email:** [support@skinergy.lol](mailto:support@skinergy.lol)

*Disclaimer: Skinergy isn't endorsed by Riot Games and doesn't reflect the views or opinions of Riot Games or anyone officially involved in producing or managing League of Legends.*

---

## Tech Stack
- **Frontend:** Next.js, React, TailwindCSS
- **Backend:** Supabase (PostgreSQL + Realtime WebSockets)
- **Desktop Uploader:** Python (Tkinter)
- **Data Sources:** Riot's Data Dragon (ddragon), CDragon, and approved community APIs.

---

## Author
Designed, developed, and deployed by [**Kenji Berry**](https://github.com/kenji-berry).

## Special Thanks
A huge thank you to the following for helping improve Skinergy:
- Arielle - for UI and clarity suggestions
- [Armin](https://github.com/ashahnami) - for structure and UX suggestions
- And everyone else who contributed feedback, testing, or ideas
