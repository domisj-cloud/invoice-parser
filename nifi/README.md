# NiFi Notes

This PoC uses a manual NiFi flow instead of committing an exported flow definition. That keeps the first iteration readable and makes it easy to adjust processor properties in the UI.

The root `README.md` contains the processor-by-processor setup:

```text
GetFile -> UpdateAttribute -> PutS3Object -> ReplaceText -> InvokeHTTP
```

The important event-based behavior is that `InvokeHTTP` is called only after `PutS3Object` succeeds.
