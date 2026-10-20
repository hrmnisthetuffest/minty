"""To-do list and ideas: the places a brain dump lands."""
from minty_core.butler.domain import Domain

TASKS = Domain(name="tasks", label="to-do list", noun="task", remind="To do: {name}.",
               aliases=("to-do", "todo", "to do", "tasks", "task list", "to-do list", "todo list"))
IDEAS = Domain(name="ideas", label="ideas list", noun="idea", aliases=("ideas", "idea list"), remind="")
