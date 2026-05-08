Decompose the implementation specification into a sequence of independently-shippable steps.

Each step should:
- Be small enough to land as one PR (typically a few files, a focused change).
- Have a clear pre- and post-condition the implementer can verify.
- List the files it touches, the tests it adds or updates, and the verification command(s).
- Be ordered such that earlier steps are merged before later steps depend on them.

Set the plan's `count` field to the number of steps. The implementation loop will run once per step. Do not produce more steps than the impl spec actually warrants; one step is acceptable when the change is genuinely atomic.
