import marimo

__generated_with = "0.23.6"
app = marimo.App(width="full")


@app.cell
def _():
    import marimo as mo
    import time

    alert_text, set_alert_text = mo.state(None)
    return alert_text, mo, set_alert_text, time


@app.cell
def _(mo, set_alert_text, time):
    def show_temporary_alert(message, duration=3):
        set_alert_text(message)

        def timer():
            time.sleep(duration)
            set_alert_text(None) # Clears the alert

        mo.Thread(target=timer).start()

    return (show_temporary_alert,)


@app.cell
def _(mo, show_temporary_alert):
    btn = mo.ui.button(
        label="Trigger Alert",
        on_click=lambda _: show_temporary_alert("This disappears in 3 seconds!")
    )
    return (btn,)


@app.cell
def _(alert_text, btn, mo):
    mo.vstack([
        btn,
        mo.callout(alert_text(), kind="warn") if alert_text() else ""
    ])
    return


if __name__ == "__main__":
    app.run()
