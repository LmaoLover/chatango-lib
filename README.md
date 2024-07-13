# chatango-lib

Chatango bot library using Python 3.8 and asyncio

### Acknowledgements

Credit to the original project [neokuze/chatango-lib](https://github.com/neokuze/chatango-lib) by authors: [neokuze](https://github.com/neokuze/chatango-lib) and [TheClonerx](https://github.com/TheClonerx)

## Installation

### Requirements

 - `python` 3.8+
 - `pip`

### From Source
```
git clone https://github.com/LmaoLover/chatango-lib
cd chatango-lib
pip install .
```

## Usage

The main classes you will use from the `chatango` module are `Client`, `Room`, `PM`, `Message`, and `User`.

*Please Note*: the interfaces for these classes and their events are not finalized.  Please be aware of any changes when upgrading.

### Using `asyncio`

The major difference from older libraries like `ch.py` is the use of `asyncio`. `async` functions must either be `await`ed which cause the awaiting function to pause until the operation completes, or made into a task which runs in parallel to the current control flow.

`Client` and `Room` provide some light task handlers for convenience.  Within your custom subclasses you can use `self.add_task(...)` instead of `asyncio.create_task(...)`.  This provides some basic exception reporting, plus custom error handling via `self.on_task_exception(task)`.

### Events

All generated events are async coroutines ran as tasks on the `Room` or `Client` which defined them.

### Limitations of `asyncio`

Regular blocking I/O cannot be used in async programs as it will block the execution of all routines.  Also, any slow CPU heavy code will block other routines from running.

You can still use regular synchronous I/O by running it in a separate thread.  The easiest way to do this is using `asyncio.to_thread`:

```
# This code will block the entire application
res = requests.get("example.com")

# Run instead in an async friendly thread
res = await asyncio.to_thread(requests.get, "example.com")
```

This should be used only with single I/O calls, as you cannot call or access any async code from within the new thread.

You can run slow CPU heavy calculations in a separate python `Process` if necessary.

### Basic Example

A common pattern similar to older libraries like `ch.py` is to create a custom `Client` class to connect and handle events from multiple rooms.

See `example.py`.

### Custom Room

You may create a custom `Room` subclass to handle events.  This also allows your application to add custom attributes to `Room`.

If you are only connecting to one room, or want to manage rooms yourself without a `Client`, you may do so using a custom `Room` subclass.

