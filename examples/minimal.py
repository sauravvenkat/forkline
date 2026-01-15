from forkline import Tracer, replay


def main() -> None:
    tracer = Tracer()
    with tracer:
        with tracer.step("plan"):
            tracer.record_event("input", {"prompt": "hello"})
        with tracer.step("execute"):
            tracer.record_event("output", {"result": "world"})

    run = replay(tracer.run_id, tracer.store)
    print(run)


if __name__ == "__main__":
    main()
