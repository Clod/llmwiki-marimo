import marimo

__generated_with = "0.23.6"
app = marimo.App(width="full")


@app.cell
def _():
    import marimo as mo

    return (mo,)


@app.cell
def _(mo):
    step, set_step = mo.state(0)        # 0=idle  1=pending  2=decided
    decision, set_decision = mo.state(None)
    return decision, set_decision, set_step, step


@app.cell
def _(mo, set_decision, set_step):
    # No reference to `step` — this cell always runs, delete button stays visible
    delete_btn = mo.ui.button(
        label="Delete item",
        kind="danger",
        on_change=lambda _: (set_step(1), set_decision(None)),
    )
    delete_btn
    return


@app.cell
def _(mo, set_decision, set_step, step):
    mo.stop(step() != 1)
    confirm = mo.ui.button(
        label="Confirm",
        kind="danger",
        on_change=lambda _: (set_decision(True), set_step(2)),
    )
    cancel = mo.ui.button(
        label="Cancel",
        on_change=lambda _: (set_decision(False), set_step(2)),
    )
    mo.hstack([cancel, confirm])
    return


@app.cell
def _(decision, mo, step):
    mo.stop(step() != 2)
    mo.md("✅ Action completed.") if decision() else mo.md("❌ Cancelled.")
    return


if __name__ == "__main__":
    app.run()
