import AuthShell from '../../components/auth/AuthShell'

export const metadata = {
  title: 'Entrar',
  description: 'Inicia sesión en Don’tRipIt para gestionar tu colección y wishlist.',
}

export default async function LoginPage({ searchParams }) {
  const params = await searchParams
  return <AuthShell mode="login" next={params?.next || '/dashboard'} />
}
