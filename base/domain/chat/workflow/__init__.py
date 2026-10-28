"""The workflows of the chat checks, declared with pydantic-graph `GraphBuilder`.

`input_checks` holds the checks of the question of the pre-retrieval mode;
`output_checks` holds the checks of the answer of the pre-retrieval and the
strict modes. The call to the agent runs between the two workflows, outside both.
"""
