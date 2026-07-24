import { SignIn } from "@clerk/nextjs";

export default function SignInPage() {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-8 px-4 py-16">
      <div className="flex flex-col items-center gap-2">
        <span className="text-lg font-semibold tracking-tight text-black">
          Rufo
        </span>
        <p className="text-sm text-black/60">Sign in to your organization</p>
      </div>
      <SignIn
        appearance={{
          elements: {
            rootBox: "w-full max-w-sm",
            card: "shadow-sm border border-black rounded-xl",
            headerTitle: "hidden",
            headerSubtitle: "hidden",
            socialButtonsBlockButton: "border border-black hover:bg-black/5",
            formButtonPrimary: "bg-black hover:bg-black/80 text-sm normal-case",
            footerActionLink: "text-black hover:text-black/70",
          },
          variables: {
            colorPrimary: "#000000",
            colorBackground: "#ffffff",
            borderRadius: "0.625rem",
          },
        }}
      />
    </div>
  );
}
