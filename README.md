# Skinergy  
***Currently in **alpha release**, with core functionality live and ongoing improvements guided by user feedback.***  

**Skinergy** is a League of Legends **skin collection and comparison app with real‑time social features**.  
Players can upload their owned skins, track their collection, and see other users’ activity live without refreshing the page.  

Skinergy allows multiple users to create and compare skin sets in real time, blending personal collection tracking with shared social interaction.  

---

## ✨ Features

- **Skin Collection Management**  
  Upload and track your owned skins using Riot’s Data Dragon (ddragon), along with API integrations and approved scraping sources.  
  Skinergy also provides enhanced skin metadata and statistics that are not available in the standard League client, offering deeper insight into your collection.

- **Live Sessions and Skin Matching Algorithm**  
  Users can create or join live sessions with up to five participants, reflecting the size of a League team.  
  Each participant selects their preferred roles and champions, and the session host chooses a filtering mode:  
  - Strict filtering includes only primary-role champions (for example, Poppy as support)  
  - Flexible filtering allows off-role champions (for example, Poppy as jungle)

  The session is powered by WebSockets, which provide continuous live updates. This means participants can see each other’s availability in real time, including ready, unready and away states, without needing to refresh the page.  
  The algorithm then analyses all skins owned by the session participants and generates permutations of matching skin sets.  
  For example, if all five users own different Infernal skins, that would form a valid permutation.  
  These permutations are scored and ranked based on relevance, theme cohesion and user preferences, helping players discover the strongest team-wide skin combinations.  
  Results can be filtered and refined instantly within the session.

- **Secure Skin Uploader (Desktop App)**  
  - Downloadable Tkinter-based desktop app for uploading skins  
  - Each download generates a unique code tied to your account, ensuring uploads are securely linked to your ID  
  - Automatically updates or inserts new skins since your last upload  
  - Planned to be open source within this repository for transparency and trust. Even though the code is public, the unique code system ensures it cannot be exploited


---

## Tech Stack
- **Frontend:** Next.js, React, TailwindCSS  
- **Backend:** Supabase (PostgreSQL + Realtime)  
- **Desktop Uploader:** Python (Tkinter)  
- **Data Sources:** Riot’s Data Dragon (ddragon), APIs, and approved scraping sources  


---

## Deployment & SEO
- Deployed on Vercel with the custom domain [skinergy.lol](https://www.skinergy.lol/).  

---

## Notes
- The **Skin Uploader desktop app** will be open sourced within this repo to ensure transparency and user trust.  
- Currently in **alpha release**, with ongoing improvements guided by user feedback.  

---
## Author
Independently designed, developed, and deployed by **Kenji Berry**.
