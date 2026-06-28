import { redirect } from "next/navigation";

// The lab launcher is the home page.
export default function Home() {
  redirect("/lab/interview-v3");
}
