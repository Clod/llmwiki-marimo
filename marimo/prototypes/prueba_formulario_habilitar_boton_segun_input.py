import marimo

__generated_with = "0.23.6"
app = marimo.App(width="full")


@app.cell
def _():
    import marimo as mo

    return (mo,)


@app.cell
def _():
    prohibidas = ["uno", "dos"]
    return (prohibidas,)


@app.cell
def _(mo):
    texto1 = mo.ui.text_area(placeholder="...xx")
    return


@app.cell
def _(mo):
    get_texto, set_texto = mo.state("")

    texto = mo.ui.text_area(
        placeholder="...xx",
        debounce=False,
        on_change=set_texto,
    )

    texto
    return get_texto, texto


@app.cell
def _(get_texto, mo, prohibidas, texto):
    algo = get_texto()

    print(algo)

    boton = mo.ui.button(
        label="Clod",
        tooltip="Pirulo",
        disabled=(texto.value.strip() == "" or texto.value in prohibidas),
    )
    boton
    return


if __name__ == "__main__":
    app.run()
