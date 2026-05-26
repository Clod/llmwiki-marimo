import marimo

__generated_with = "0.23.6"
app = marimo.App(width="full")


@app.cell
def _():
    import marimo as mo

    name = mo.ui.text(label="Name", placeholder="Enter at least 3 characters")
    age = mo.ui.number(label="Age", start=0, stop=120, value=18)

    def validate_registration(v):
        if len(v["name"]) < 3:
            return "Name must be at least 3 characters"
        if v["age"] < 18:
            return "Age must be 18 or older"
        return None

    form = (
        mo.md(
            """
            ## Registration

            Name: {name}
            Age: {age}
            """
        )
        .batch(name=name, age=age)
        .form(
            submit_button_label="Submit",
            validate=validate_registration,
        )
    )

    form
    return (form,)


@app.cell
def _(form):
    form.value
    return


@app.cell
def _(form):
    form.value["name"]
    return


@app.cell
def _(form):
    form.value["age"]
    return


if __name__ == "__main__":
    app.run()
