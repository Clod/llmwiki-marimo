import anywidget
import traitlets


class DeleteConfirmWidget(anywidget.AnyWidget):
    """Delete button with an inline confirmation panel.

    The widget is entirely self-contained: the delete button, the confirmation
    panel, and all show/hide logic live in the JS layer. Python only needs to
    watch `event_id` to detect a confirmed deletion.

    Traits:
        label       — item name used in default button text and message
        button_label — overrides the trigger button text (default: "Delete {label}")
        message     — overrides the panel message (default: "Delete {label}? ...")
        disabled    — grays out and disables the trigger button
        is_open     — whether the confirmation panel is currently showing
        event_id    — increments on every confirmed deletion; watch this in Python

    Usage::

        widget = mo.ui.anywidget(DeleteConfirmWidget(label="my-page"))
        widget  # renders in the cell

        # in a separate cell, watch for new confirmations:
        if widget.event_id > last_handled:
            last_handled = widget.event_id
            do_deletion()
    """

    _esm = r"""
    function render({ model, el }) {
      el.innerHTML = "";

      const root = document.createElement("div");
      root.className = "dc-root";

      const deleteBtn = document.createElement("button");
      deleteBtn.className = "dc-delete";
      deleteBtn.type = "button";

      const panel = document.createElement("div");
      panel.className = "dc-panel";
      panel.style.display = "none";

      const message = document.createElement("div");
      message.className = "dc-message";

      const actions = document.createElement("div");
      actions.className = "dc-actions";

      const cancelBtn = document.createElement("button");
      cancelBtn.className = "dc-cancel";
      cancelBtn.type = "button";
      cancelBtn.textContent = "Cancel";

      const confirmBtn = document.createElement("button");
      confirmBtn.className = "dc-confirm";
      confirmBtn.type = "button";
      confirmBtn.textContent = "Confirm";

      actions.appendChild(cancelBtn);
      actions.appendChild(confirmBtn);
      panel.appendChild(message);
      panel.appendChild(actions);
      root.appendChild(deleteBtn);
      root.appendChild(panel);
      el.appendChild(root);

      function getBtnText() {
        const override = model.get("button_label");
        return override || `Delete ${model.get("label")}`;
      }

      function getMsg() {
        const override = model.get("message");
        return override || `Delete ${model.get("label")}? This cannot be undone.`;
      }

      function syncView() {
        const isOpen = model.get("is_open");
        const disabled = model.get("disabled");
        deleteBtn.textContent = getBtnText();
        deleteBtn.disabled = disabled;
        message.textContent = getMsg();
        panel.style.display = isOpen ? "block" : "none";
      }

      deleteBtn.addEventListener("click", () => {
        if (model.get("disabled")) return;
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
      model.on("change:button_label", syncView);
      model.on("change:message", syncView);
      model.on("change:is_open", syncView);
      model.on("change:disabled", syncView);

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

    .dc-root button:disabled {
      opacity: 0.45;
      cursor: not-allowed;
    }

    .dc-delete,
    .dc-confirm {
      background: #b42318 !important;
      color: white !important;
      border-color: #b42318 !important;
    }

    .dc-cancel {
      background: #eef2f7 !important;
      border-color: #d0d7de !important;
      color: #24292f !important;
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

    label = traitlets.Unicode("").tag(sync=True)
    button_label = traitlets.Unicode("").tag(sync=True)
    message = traitlets.Unicode("").tag(sync=True)
    disabled = traitlets.Bool(False).tag(sync=True)
    is_open = traitlets.Bool(False).tag(sync=True)
    event_id = traitlets.Int(0).tag(sync=True)
