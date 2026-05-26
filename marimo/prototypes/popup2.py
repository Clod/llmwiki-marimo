import marimo

__generated_with = "0.23.6"
app = marimo.App()


@app.cell
def _():
    import marimo as mo

    get_shown, set_shown = mo.state(True)
    return get_shown, set_shown


@app.cell
def _(set_shown):
    _dismiss = mo.ui.button(
        label="Dismiss",
        kind="neutral",
        on_click=lambda _: set_shown(False)
    )
    _dismiss
    return


@app.cell
def _(set_shown):
    _dismiss = mo.ui.button(
        label="Show",
        kind="neutral",
        on_click=lambda _: set_shown(True)
    )
    _dismiss
    return


@app.cell
def _(get_shown, mo):
    mo.md("""
    <div style="
        padding: 1rem;
        border: 1px solid #ccc;
        border-radius: 12px;
        background: #fff8dc;
        box-shadow: 0 4px 16px rgba(0,0,0,0.12);
        margin-bottom: 1rem;">
        <strong>Notification</strong><br>
        Your operation completed successfully.
    </div>
    """) if get_shown() else None
    return


if __name__ == "__main__":
    app.run()
