# read_app_tabs.py — diagramas de flujo

Variante **tabs** de la read app (`marimo/read_app_tabs.py`): misma app que
`marimo/read_app.py`, pero las 3 columnas del grid se reorganizan en **dos tabs**:

- **📖 Read** — wiki picker + navegador de páginas + lector (sidebar + reader en un flex row)
- **💬 Chat** — el chat a ancho completo (para que tablas de advisory y respuestas largas respiren)

Es aditiva: `read_app.py` + `layouts/read_app.grid.json` quedan intactos. Si tabs
ganan, esta variante reemplazará a `read_app.py`.

> **Contrato de reactividad (la razón por la que esto es seguro):**
> el selector de tabs usa `mo.ui.radio` (controlado, no `mo.ui.tabs`), así
> `tab_body` renderiza **solo** la pestaña activa. `mo.ui.tabs` construiría ambos
> cuerpos y los reconstruiría en cada re-run, re-parenteando el `mo.ui.chat` e
> invalidando su callback (`Could not find function ... send_prompt`).

Celdas (27) y módulos `domain/` referenciados: `domain.chat.agent`,
`domain.chat.config`, `domain.chat.guardrail`, `domain.chat.preretrieval`,
`domain.chat.postprocess`, `domain.chat.history`, `domain.chat.trace`,
`domain.chat.wiki_tools`, `domain.tools.wiki_fs`, `domain.wiki_registry`,
`domain.finance_argentina.agent_tool`, `widgets.delete_confirm`.

---

## 1. Grafo reactivo de celdas

Quién produce qué y quién se re-ejecuta cuando cambian los `mo.state`.

```mermaid
flowchart TD
    classDef boot fill:#eef2ff,stroke:#6366f1,color:#1e1b4b
    classDef st fill:#fff4d6,stroke:#d97706,color:#7c2d12
    classDef cell fill:#ecfdf5,stroke:#059669,color:#064e3b
    classDef out fill:#fdf2f8,stroke:#db2777,color:#831843

    subgraph BOOT["🔧 app.setup — corre 1 vez"]
        B1["load_dotenv · logging · sys.path base/ + marimo/"]:::boot
        B2["imports: config · domain.chat.agent · domain.chat.config<br/>domain.wiki_registry · widgets.delete_confirm"]:::boot
        B3["ENV_DEFAULT ← WIKI_PATH env<br/>WIKI_HOME ← resolve_wiki_home"]:::boot
        B1 --> B2 --> B3
    end

    STY["styles() — CSS: .prose · min-width:0 · tablas con overflow-x"]:::cell

    subgraph WSEL["📚 Selección de wiki"]
        WS["wiki_state()<br/>state: active_wiki · recent_list"]:::st
        WP["wiki_picker() → picker_view<br/>dropdown 📚 Wiki sobre merge_options"]:::out
        WA["wiki_add() → add_view<br/>accordion: add_path + run_button"]:::out
        WAR["wiki_add_runner() → add_result<br/>clean_path_input → resolve → ¿is_dir?"]:::cell
        WC["wiki_context(active_wiki) →<br/>WIKI_PATH · wiki_db_path · wiki_chat_config<br/>wiki_agent · wiki_agent_preret"]:::cell
        WS --> WP
        WS --> WAR
        WA --> WAR
        WAR -.->|"set_active_wiki · push_recent"| WS
        WS -->|"cambio de wiki → re-run"| WC
    end

    B3 --> WS
    B3 -->|"WIKI_HOME"| WP

    subgraph READ["📖 Rama Read"]
        WH["wiki_helpers(WIKI_PATH)<br/>mkdir wiki/ · scan_pages() · read_page()"]:::cell
        PS["page_state(scan_pages)<br/>page_list · selected_page · prev_page<br/>delete_trigger · last_delete_event · navigate_to"]:::st
        LP["left_panel() → left_view<br/>tabla Title/Path single-select + ⟳ Refresh"]:::out
        DWC["delete_widget_cell() → delete_widget<br/>anywidget DeleteConfirmWidget"]:::out
        DEC["delete_event_cell()<br/>event_id &gt; last → set_delete_trigger"]:::cell
        DR["delete_runner() → delete_result<br/>wiki_fs.delete_page · rescan · selected=None"]:::cell
        CP["current_page() → current_content · selected_stem"]:::cell
        PLN["page_links_nav() → nav_widget<br/>regex links · posixpath · ← back + botones"]:::cell
        MP["middle_panel() → middle_view<br/>título + div 70vh scroll + nav"]:::out
        WH --> PS
        PS --> LP
        PS --> DWC
        PS --> CP
        DWC --> DEC
        DEC -.->|"delete_trigger"| DR
        PS --> DR
        WH -->|"scan_pages"| DR
        CP --> PLN
        PS --> PLN
        CP --> MP
        PLN --> MP
        LP -.->|"navigate_to(stem)"| PS
    end

    WC --> WH
    WC -->|"WIKI_PATH · wiki_db_path"| DR

    subgraph CHAT["💬 Rama Chat"]
        GF["guardrail_flag()<br/>dict mutable strict + pre_retrieval<br/>(NO es mo.state)"]:::st
        GT["guardrail_toggle() → strict_view<br/>checkbox Strict mode"]:::out
        PRT["pre_retrieval_toggle() → pre_retrieval_view<br/>re-siembra default del wiki"]:::out
        CHP["chat_panel() → chat_view · last_response<br/>mo.ui.chat(respond, prompts, max_height=720)"]:::out
        GF --> GT
        GF --> PRT
        GF -.->|"leído en vivo dentro de respond()"| CHP
    end

    WC -->|"agentes + config + db"| CHP
    WC -->|"wiki_chat_config.pre_retrieval"| PRT

    subgraph SAVE["💾 Save-to-wiki"]
        SFS["save_feedback_state()<br/>save_tick · saved_notice"]:::st
        SF["save_form(last_response, save_tick) → form<br/>title + category · validate"]:::out
        SA["save_action()<br/>OpenAI client · save_to_wiki(...)"]:::cell
        SN["save_notice() → save_notice_view<br/>callout ✅ / ❌"]:::out
        SFS --> SF
        SF --> SA
        SFS --> SN
        SA -.->|"set_saved_notice · set_save_tick+1"| SFS
    end

    CHP -->|"last_response"| SF
    WC -->|"WIKI_PATH · db · language"| SA

    subgraph ASM["🧩 Ensamblado final"]
        TS["tab_switch() → tab_choice<br/>mo.ui.radio 📖 Read / 💬 Chat<br/>(sin deps → estable)"]:::out
        TB["tab_body() — renderiza SOLO la pestaña activa<br/>Chat: hstack(toggles) + chat_view<br/>Read: hstack(sidebar, middle_view) 1 / 1.5"]:::out
        SAREA["save_area() — accordion solo bajo 💬 Chat<br/>form + save_notice_view"]:::out
        TS --> TB
        TS --> SAREA
    end

    WP --> TB
    WA --> TB
    WAR --> TB
    LP --> TB
    DWC --> TB
    DR --> TB
    MP --> TB
    GT --> TB
    PRT --> TB
    CHP -->|"chat_view estable"| TB
    CHP --> TB
    SF --> SAREA
    SN --> SAREA

    TB -.->|"NO depende de form/last_response<br/>→ el chat no se re-parentea por turno"| SF
```
<!-- __CHUNK2__ -->