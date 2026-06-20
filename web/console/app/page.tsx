"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

// /live is the default single screen (doc22:329). Static export can't server-redirect, so the
// index does a client replace.
export default function Home() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/live");
  }, [router]);
  return null;
}
