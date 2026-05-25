import marimo

__generated_with = "0.23.6"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo
    import anywidget
    import traitlets


    class DeleteConfirmWidget(anywidget.AnyWidget):
        _esm = r"""
        function render({ model, el }) {
          el.innerHTML = "";

          const root = document.createElement("div");
          root.className = "dc-root";

          const deleteBtn = document.createElement("button");
          deleteBtn.className = "dc-delete";
          deleteBtn.type = "button";
          deleteBtn.textContent = `Delete ${model.get("label")}`;

          const panel = document.createElement("div");
          panel.className = "dc-panel";
          panel.style.display = "none";

          const message = document.createElement("div");
          message.className = "dc-message";

          const actions = document.createElement("div");
          actions.className = "dc-actions";

          const confirmBtn = document.createElement("button");
          confirmBtn.className = "dc-confirm";
          confirmBtn.type = "button";
          confirmBtn.textContent = "Confirm";

          const cancelBtn = document.createElement("button");
          cancelBtn.className = "dc-cancel";
          cancelBtn.type = "button";
          cancelBtn.textContent = "Cancel";

          actions.appendChild(confirmBtn);
          actions.appendChild(cancelBtn);
          panel.appendChild(message);
          panel.appendChild(actions);

          root.appendChild(deleteBtn);
          root.appendChild(panel);
          el.appendChild(root);

          function syncView() {
            const label = model.get("label");
            const isOpen = model.get("is_open");
            deleteBtn.textContent = `Delete ${label}`;
            message.textContent = `Delete ${label}? This cannot be undone.`;
            panel.style.display = isOpen ? "block" : "none";
          }

          deleteBtn.addEventListener("click", () => {
            model.set("is_open", true);
            model.save_changes();
          });

          cancelBtn.addEventListener("click", () => {
            model.set("is_open", false);
            model.save_changes();
          });

          confirmBtn.addEventListener("click", () => {
            model.set("is_open", false);
            model.set("event_id", model.get("event_id") + 1);
            model.save_changes();
          });

          model.on("change:label", syncView);
          model.on("change:is_open", syncView);
          // event_id changes don't affect the view; no listener needed

          syncView();
        }

        export default { render };
        """

        _css = r"""
        .dc-root {
          display: inline-flex;
          flex-direction: column;
          align-items: flex-start;
          gap: 8px;
          font-family: ui-sans-serif, system-ui, sans-serif;
        }

        .dc-root button {
          border: 1px solid #d0d7de;
          border-radius: 8px;
          padding: 6px 12px;
          cursor: pointer;
          background: white;
          font-size: 14px;
        }

        .dc-root .dc-delete,
        .dc-root .dc-confirm {
          background: #b42318;
          color: white;
          border-color: #b42318;
        }

        .dc-root .dc-cancel {
          background: #eef2f7;
          border-color: #d0d7de;
          color: #24292f;
        }

        .dc-panel {
          border: 1px solid #d0d7de;
          border-radius: 10px;
          padding: 10px;
          background: #fafafa;
          min-width: 280px;
          box-shadow: 0 2px 10px rgba(0, 0, 0, 0.08);
        }

        .dc-message {
          margin-bottom: 8px;
          font-size: 14px;
        }

        .dc-actions {
          display: flex;
          gap: 8px;
        }
        """

        label = traitlets.Unicode("report.csv").tag(sync=True)
        is_open = traitlets.Bool(False).tag(sync=True)
        event_id = traitlets.Int(0).tag(sync=True)


    delete_widget = mo.ui.anywidget(DeleteConfirmWidget(label="report.csv"))
    delete_widget
    return delete_widget, mo


@app.cell
def _(mo):
    get_last_handled_event, set_last_handled_event = mo.state(0)
    get_busy, set_busy = mo.state(False)
    get_status, set_status = mo.state("")
    return (
        get_busy,
        get_last_handled_event,
        get_status,
        set_busy,
        set_last_handled_event,
        set_status,
    )


@app.cell
def _(
    delete_widget,
    get_last_handled_event,
    set_busy,
    set_last_handled_event,
    set_status,
):
    event_id = delete_widget.event_id
    last_handled = get_last_handled_event()

    if event_id > last_handled:
        set_last_handled_event(event_id)
        set_busy(True)
        try:
            # put the real destructive action here
            # e.g. os.remove("report.csv")
            set_status(f"Deleted on event {event_id}.")
        except Exception as e:
            set_status(f"Delete failed: {e}")
        finally:
            set_busy(False)
    return


@app.cell
def _(delete_widget, get_busy, get_last_handled_event, get_status, mo):
    mo.md(f"""
    busy: `{get_busy()}`  
    event_id: `{delete_widget.event_id}`  
    last_handled: `{get_last_handled_event()}`  
    status: `{get_status()!r}`
    """)
    return


if __name__ == "__main__":
    app.run()
