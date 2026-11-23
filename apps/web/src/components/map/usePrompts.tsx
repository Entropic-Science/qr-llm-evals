import { QueryClient, QueryClientContext, QueryClientProvider, useInfiniteQuery } from "@tanstack/react-query";
import { useContext, useState, type ReactNode } from "react";
import { ApiError, api } from "../../lib/api";
import { PROMPT_PAGE, failureSentence, nextOffset, type Told } from "../../lib/map/prompts";

// The prompts of an eval, a page at a time, from the run service's card-items route. A page is asked for only
// once the list is scrolled near its end; what has been read is kept while the map is on screen.

/**
 * Gives the map a place to keep the pages it has read. Inside the app that is the page's own query client;
 * where the map is mounted without one (its tests, an embed) it brings its own, for as long as it is mounted.
 */
export function PromptQueries({ children }: { children: ReactNode }) {
  const outer = useContext(QueryClientContext);
  const [own] = useState(() => (outer === undefined ? new QueryClient() : null));
  return own === null ? <>{children}</> : <QueryClientProvider client={own}>{children}</QueryClientProvider>;
}

/**
 * The prompts of one eval in one told cell. The first page is read at once, the next when `fetchNextPage` is
 * called. A request that fails is a state to show, with a way to try again; it is not retried in silence, by
 * the library (`retry: false`) or by the list, which stops asking by itself once a page has failed.
 */
export function useCardItems(cardId: string, told: Told | null, enabled = true) {
  return useInfiniteQuery({
    queryKey: ["card-items", cardId, told],
    queryFn: ({ pageParam, signal }) => api.cardItems(cardId, { offset: pageParam, limit: PROMPT_PAGE, told }, signal),
    initialPageParam: 0,
    getNextPageParam: nextOffset,
    enabled,
    retry: false,
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
  });
}

/** What to say about a request for prompts that failed. */
export function promptFailure(error: unknown): string {
  return failureSentence(error instanceof ApiError ? { status: error.status, message: error.message } : null);
}
