export default function RegisterLayout({ children }) {
  return (
    <>
      <style>{`label:has(input[name="marketing_consent"]) { display: none !important; }`}</style>
      {children}
    </>
  )
}
