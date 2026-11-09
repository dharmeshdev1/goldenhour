import Link from "next/link";

export default function NotFound() {
  return (
    <div className="shell flex min-h-[70vh] flex-col items-start justify-center py-24">
      <p className="eyebrow mb-4 text-gold">404</p>
      <h1 className="display max-w-3xl text-[52px] text-ink sm:text-[72px]">The trail goes <em className="text-gold">cold</em> here.</h1>
      <p className="lead mt-5 max-w-xl">This page does not exist. The money is still moving, though.</p>
      <div className="mt-8 flex flex-wrap gap-3">
        <Link href="/" className="btn btn-gold">
          Back to the war room
        </Link>
        <Link href="/trail/" className="btn btn-ghost">
          Follow the trail
        </Link>
      </div>
    </div>
  );
}
