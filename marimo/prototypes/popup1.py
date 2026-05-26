import marimo

__generated_with = "0.23.6"
app = marimo.App()


@app.cell
def _():
    import marimo as mo

    show_notice = mo.ui.checkbox(label="Show notice", value=True)
    dismiss = mo.ui.checkbox(label="Dismiss", value=False)
    mo.vstack([show_notice, dismiss])
    return dismiss, show_notice


@app.cell
def _(dismiss, show_notice):
    visible = show_notice.value and not dismiss.value
    notice = (
        mo.md("**Heads up:** this is a lightweight dismissible popup.").callout(kind="info")
        if visible
        else mo.md("")
    )
    notice
    return


if __name__ == "__main__":
    app.run()
