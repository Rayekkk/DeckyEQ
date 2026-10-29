/** Browser-only adapter for the shared ui.tsx (toasts); index.tsx's RPCs are mocked by each test that loads it. */
export const toaster = { toast: ({ title, body }: { title: string; body: string }) => console.info(`[toast] ${title}: ${body}`) };
