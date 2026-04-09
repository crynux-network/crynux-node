## Coding Requirements

For Web UI error handling, never silently swallow errors. Handle errors by type: for user-impacting or recoverable cases, show clear Ant Design Vue v4 feedback with a safe fallback or retry path; otherwise log structured context to the console and rethrow or propagate as appropriate.

When implementing page components in Vue, use standard `ant-design-vue` 4.x components by default. Do not introduce custom styles unless there is a clear special requirement; prefer built-in component props and standard styles.
